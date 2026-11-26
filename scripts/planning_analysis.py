"""Per-cell planning indicators computed from rasters and the road network.

Used by scripts/build_db.py. Every indicator is calculated from data:

* population 2020 / 2025 / 2030 - WorldPop R2025A constrained 100 m rasters
  (2030 is WorldPop's projection), summed over pixels whose centre is in the cell;
* road access - share of 2025 residents living within ROAD_ACCESS_KM of a classified
  road, OSM trunk to tertiary (buffer built in DuckDB Spatial SQL, see build_db.py);
* flood exposure - share of the cell's area and of its residents inside the
  JRC GloFAS 1-in-100-year river flood extent or permanent water;
* travel time to care - walking from the cell's population centre to the nearest
  road, then the quickest road route (motorcycle speeds) to the nearest facility.
"""

from __future__ import annotations

import heapq
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask, rasterize
from rasterio.mask import mask
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
POPULATION_PATHS = {
    2020: RAW / "dedza_pop_2020_CN_100m_R2025A_v1.tif",
    2025: RAW / "mwi_pop_2025_CN_100m_R2025A_v1.tif",
    2030: RAW / "dedza_pop_2030_CN_100m_R2025A_v1.tif",
}
FLOOD_DEPTH_PATH = RAW / "dedza_flood_rp100_depth.tif"
PERMANENT_WATER_PATH = RAW / "dedza_permanent_water.tif"
PLANNING_LAYER_PATHS = [*POPULATION_PATHS.values(), FLOOD_DEPTH_PATH, PERMANENT_WATER_PATH]

ROAD_ACCESS_KM = 2.0
WALKING_SPEED_KMH = 5.0
MAX_TRAVEL_MIN = 24 * 60


def _aligned(path: Path, shape: tuple[int, int], transform, crs, fill: float) -> np.ndarray:
    """Resample a raster onto the reference grid (nearest neighbour, nodata -> fill)."""
    out = np.full(shape, fill, dtype=np.float32)
    with rasterio.open(path) as source:
        reproject(
            source=rasterio.band(source, 1),
            destination=out,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=transform,
            dst_crs=crs,
            dst_nodata=fill,
            resampling=Resampling.nearest,
        )
    out[~np.isfinite(out)] = fill
    return out


def planning_grids(district: dict) -> dict:
    """Population, flood and district grids aligned to the WorldPop 2025 pixels."""
    with rasterio.open(POPULATION_PATHS[2025]) as source:
        clipped, transform = mask(source, [district], crop=True, filled=True, nodata=source.nodata)
        crs = source.crs
        nodata = source.nodata
    pop_2025 = clipped[0].astype(np.float32)
    shape = pop_2025.shape
    inside = geometry_mask([district], out_shape=shape, transform=transform, invert=True)
    pop_2025[~np.isfinite(pop_2025) | (pop_2025 < 0) | (pop_2025 == nodata)] = 0
    population = {2025: np.where(inside, pop_2025, 0)}
    for year in (2020, 2030):
        grid = _aligned(POPULATION_PATHS[year], shape, transform, crs, 0)
        grid[grid < 0] = 0
        population[year] = np.where(inside, grid, 0)
    depth = _aligned(FLOOD_DEPTH_PATH, shape, transform, crs, 0)
    permanent = _aligned(PERMANENT_WATER_PATH, shape, transform, crs, 0)
    flooded = inside & ((depth > 0) | (permanent == 1))
    return {
        "shape": shape,
        "transform": transform,
        "inside": inside,
        "population": population,
        "flooded": flooded,
    }


def cell_indicators(grids: dict, cells: list[tuple[int, str]], road_buffer: dict) -> dict[int, dict]:
    """Population, growth, road-access and flood indicators for each cell."""
    shape, transform, inside = grids["shape"], grids["transform"], grids["inside"]
    ids = rasterize(
        [(json.loads(geojson), int(cell_id)) for cell_id, geojson in cells],
        out_shape=shape, transform=transform, fill=0, dtype="int32", all_touched=False,
    )
    near_road = geometry_mask([road_buffer], out_shape=shape, transform=transform, invert=True)
    size = max(int(cell_id) for cell_id, _ in cells) + 1
    sel = inside & (ids > 0)
    flat = ids[sel]

    def total(values: np.ndarray) -> np.ndarray:
        return np.bincount(flat, weights=values[sel].astype(np.float64), minlength=size)

    pop = grids["population"]
    rows, cols = np.indices(shape)
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e
    pixels = total(np.ones(shape))
    flooded_pixels = total(grids["flooded"].astype(np.float64))
    sums = {year: total(grid) for year, grid in pop.items()}
    near_road_pop = total(pop[2025] * near_road)
    flood_pop = total(pop[2025] * grids["flooded"])
    weighted_lon = total(pop[2025] * lon)
    weighted_lat = total(pop[2025] * lat)

    result = {}
    for cell_id, _ in cells:
        i = int(cell_id)
        p25 = sums[2025][i]
        result[i] = {
            "population_2020": float(sums[2020][i]),
            "population": float(p25),
            "population_2030": float(sums[2030][i]),
            "road_access_share": float(near_road_pop[i] / p25) if p25 > 0 else 0.0,
            "flood_pop_share": float(flood_pop[i] / p25) if p25 > 0 else 0.0,
            "flood_area_share": float(flooded_pixels[i] / pixels[i]) if pixels[i] > 0 else 0.0,
            "pop_centre": (float(weighted_lon[i] / p25), float(weighted_lat[i] / p25)) if p25 > 0 else None,
        }
    return result


def travel_minutes_to_care(
    origins: dict[int, tuple[float, float]],
    facilities: list[tuple[float, float]],
) -> dict[int, float | None]:
    """Walk to the nearest road node, then the quickest road route to any facility.

    Uses the same road graph and motorcycle speeds as the app's routing. Facility
    walking connectors are included as start offsets of a multi-source Dijkstra
    on the reversed graph, so every node gets its time to the nearest facility.
    """
    from app import haversine_km, load_road_network, nearest_road_node, reverse_road_graph

    graph, _, projected = load_road_network()
    walk = lambda km: km / WALKING_SPEED_KMH * 60  # noqa: E731
    start_times: dict[tuple[float, float], float] = {}
    for point in facilities:
        node = nearest_road_node(point, projected)
        offset = walk(haversine_km(point, node))
        start_times[node] = min(offset, start_times.get(node, float("inf")))

    reversed_graph = reverse_road_graph(graph)
    times = dict(start_times)
    queue = [(time, node) for node, time in times.items()]
    heapq.heapify(queue)
    while queue:
        time_so_far, current = heapq.heappop(queue)
        if time_so_far > times.get(current, float("inf")):
            continue
        for neighbor, _, time_min in reversed_graph.get(current, ()):
            candidate = time_so_far + time_min
            if candidate < times.get(neighbor, float("inf")) and candidate <= MAX_TRAVEL_MIN:
                times[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))

    result: dict[int, float | None] = {}
    for cell_id, point in origins.items():
        node = nearest_road_node(point, projected)
        road_time = times.get(node)
        result[cell_id] = None if road_time is None else walk(haversine_km(point, node)) + road_time
    return result
