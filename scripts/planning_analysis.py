"""Per-cell planning indicators computed from rasters and the road network.

Used by scripts/build_db.py. Every indicator is calculated from data:

* population 2020 / 2025 / 2030 - WorldPop R2025A constrained 100 m rasters
  (2030 is WorldPop's projection), summed over pixels whose centre is in the cell;
* road access - share of 2025 residents living within ROAD_ACCESS_KM of a classified
  road, OSM trunk to tertiary (buffer built in DuckDB Spatial SQL, see build_db.py);
* flood exposure - share of the cell's area and of its residents inside the
  JRC GloFAS 1-in-100-year river flood extent or permanent water;
* walking distance to care - from the cell's population centre along roads,
  tracks and paths to the nearest facility (scripts/road_network.py);
* vehicle travel time to care - walk to the nearest road, then the quickest road
  route at indicative motorcycle speeds.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask, rasterize
from rasterio.mask import mask
from rasterio.warp import Resampling, reproject

from scripts.road_network import (
    WALKING_SPEED_KMH,
    NodeIndex,
    build_graph,
    fetch_road_rows,
    reverse_graph,
    shortest_costs,
)

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


def network_access(
    origins: dict[int, tuple[float, float]],
    facilities: list[tuple[float, float]],
    route_db: Path,
) -> dict[int, dict[str, float | None]]:
    """Walking distance and vehicle travel time from each origin to the nearest facility.

    * walk_km: straight walk to the nearest node of the walking network (roads,
      tracks and paths), then the shortest walking route to a facility.
    * vehicle_min: walk to the nearest road at 5 km/h, then the quickest road
      route at indicative motorcycle speeds.
    Facility connectors (facility to its nearest node) are start offsets of a
    multi-source Dijkstra, so every node gets its cost to the nearest facility.
    """
    rows = fetch_road_rows(route_db)
    walk_min = lambda km: km / WALKING_SPEED_KMH * 60  # noqa: E731
    result: dict[int, dict[str, float | None]] = {cell_id: {} for cell_id in origins}

    walking = build_graph(rows, "walking")
    walking_index = NodeIndex(walking)
    starts: dict[tuple[float, float], float] = {}
    for point in facilities:
        node, snap = walking_index.nearest(point)
        starts[node] = min(snap, starts.get(node, float("inf")))
    distances, _ = shortest_costs(walking, starts, weight="km")
    for cell_id, point in origins.items():
        node, snap = walking_index.nearest(point)
        reached = distances.get(node)
        result[cell_id]["walk_km"] = None if reached is None else reached + snap
    del walking, walking_index, distances

    vehicle = build_graph(rows, "vehicle")
    vehicle_index = NodeIndex(vehicle)
    starts = {}
    for point in facilities:
        node, snap = vehicle_index.nearest(point)
        starts[node] = min(walk_min(snap), starts.get(node, float("inf")))
    # Times *to* a facility: search the reversed (one-way aware) graph.
    times, _ = shortest_costs(reverse_graph(vehicle), starts, weight="min", limit=MAX_TRAVEL_MIN)
    for cell_id, point in origins.items():
        node, snap = vehicle_index.nearest(point)
        reached = times.get(node)
        result[cell_id]["vehicle_min"] = None if reached is None else reached + walk_min(snap)
    return result
