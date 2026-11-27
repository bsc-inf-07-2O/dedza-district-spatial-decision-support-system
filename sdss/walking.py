"""Walking distance along mapped roads, tracks and paths.

A location's walking distance is its straight walk to the nearest network node
plus the shortest route from that node.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

import numpy as np
import shapely
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely.geometry import shape as shapely_shape
from shapely.ops import unary_union

from scripts.road_network import NodeIndex, shortest_costs

from .data import db, load_payloads
from .routing import load_road_network
from .schools import level_group


@lru_cache(maxsize=1)
def walking_index() -> NodeIndex:
    graph, _, _ = load_road_network("walking")
    return NodeIndex(graph)


def service_sites(kind: str) -> list[dict]:
    """Existing services that walking distance is measured to.

    Health uses the same 70 MOH facilities as the coverage grid (Dedza's own plus
    those within 20 km across the border); schools are Dedza's schools, all of
    them ("school") or one level ("school:primary", "school:secondary"). Each
    school keeps its position in the full school list as "index".
    """
    if kind.startswith("school"):
        level = kind.partition(":")[2]
        return [
            {"index": i, "name": s["name"], "lon": s["lon"], "lat": s["lat"]}
            for i, s in enumerate(load_payloads()["schools"])
            if not level or level_group(s["name"]) == level
        ]
    con = db()
    try:
        rows = con.execute("SELECT name, lon, lat, in_dedza FROM distance_facilities").fetchall()
    finally:
        con.close()
    if not rows:  # older database without the table
        return [{"name": f["name"], "lon": f["lon"], "lat": f["lat"]} for f in load_payloads()["facilities"]]
    return [{"name": r["name"], "lon": r["lon"], "lat": r["lat"], "in_dedza": bool(r["in_dedza"])} for r in rows]


@lru_cache(maxsize=4)
def service_walk_costs(kind: str) -> tuple[list[dict], dict, dict]:
    """Walking distance (km) from every network node to the nearest service of
    `kind`, including the walk from each service to its nearest node, and which
    service is nearest."""
    graph, _, _ = load_road_network("walking")
    index = walking_index()
    sites = service_sites(kind)
    starts: dict[tuple[float, float], float] = {}
    labels: dict[tuple[float, float], int] = {}
    nearest, distance = index.nearest_points(
        np.array([site["lon"] for site in sites], dtype=float), np.array([site["lat"] for site in sites], dtype=float)
    )
    for i, (node_i, snap) in enumerate(zip(nearest, distance)):
        node = index.nodes[node_i]
        if snap < starts.get(node, math.inf):
            starts[node], labels[node] = snap, i
    costs, origin = shortest_costs(graph, starts, weight="km", labels=labels)
    return sites, costs, origin


@lru_cache(maxsize=1)
def settlement_nodes() -> list[tuple[tuple[float, float], float]]:
    """Nearest walking-network node and the straight walk to it, per settlement."""
    index = walking_index()
    settlements = load_payloads()["settlements"]
    nearest, distance = index.nearest_points(
        np.array([float(s["lon"]) for s in settlements]), np.array([float(s["lat"]) for s in settlements])
    )
    return [(index.nodes[i], float(d)) for i, d in zip(nearest, distance)]


def walking_reach(point: tuple[float, float], limit_km: float) -> tuple[dict, float]:
    """Walking distance from `point` to every node within `limit_km`."""
    graph, _, _ = load_road_network("walking")
    node, snap = walking_index().nearest(point)
    if snap > limit_km:
        return {}, snap
    costs, _ = shortest_costs(graph, {node: snap}, weight="km", limit=limit_km)
    return costs, snap


def walking_area_geometry(
    costs: dict,
    limit_km: float,
    bounds: tuple[float, float, float, float],
    cell_km: float,
) -> dict | None:
    """Area within `limit_km` walking distance, as a GeoJSON geometry.

    Uses the same rule as the population counts: a location is within reach
    when its straight walk to the nearest path node plus that node's walking
    cost is at most `limit_km`. The rule is sampled on a `cell_km` grid over
    `bounds` (west, south, east, north) and the reachable cells are merged.
    """
    west, south, east, north = bounds
    mid_lat = (south + north) / 2
    dlat = cell_km / 110.574
    dlon = cell_km / (111.320 * math.cos(math.radians(mid_lat)))
    cols = max(1, math.ceil((east - west) / dlon))
    rows = max(1, math.ceil((north - south) / dlat))
    lon = west + (np.arange(cols) + 0.5) * dlon
    lat = north - (np.arange(rows) + 0.5) * dlat
    grid_lon, grid_lat = np.meshgrid(lon, lat)

    index = walking_index()
    # A point further than limit_km from every path node is out of reach anyway.
    nearest, snap = index.nearest_points(
        grid_lon.ravel(), grid_lat.ravel(), cell_km=max(0.5, cell_km * 2), max_km=limit_km
    )
    node_cost = np.fromiter(
        (costs.get(node, math.inf) for node in index.nodes), dtype=np.float64, count=len(index.nodes)
    )
    total = np.where(nearest >= 0, node_cost[np.maximum(nearest, 0)] + snap, math.inf)
    within = (total <= limit_km).reshape(rows, cols)
    if not within.any():
        return None

    transform = Affine(dlon, 0, west, 0, -dlat, north)
    parts = [
        shapely_shape(geometry)
        for geometry, value in shapes(within.astype(np.uint8), mask=within, transform=transform)
        if value == 1
    ]
    area = unary_union(parts).simplify(min(dlon, dlat) * 0.6, preserve_topology=True)
    return json.loads(shapely.to_geojson(shapely.set_precision(area, 1e-5)))


@lru_cache(maxsize=4)
def health_walking_area(limit_km: float = 5.0) -> dict | None:
    """Part of Dedza within `limit_km` walking distance of a baseline health
    facility (including facilities across the border), clipped to the district."""
    _, costs, _ = service_walk_costs("health")
    boundary = unary_union([
        shapely_shape(feature["geometry"]) for feature in load_payloads()["boundary"]["features"]
    ])
    geometry = walking_area_geometry(costs, limit_km, boundary.bounds, 0.25)
    if geometry is None:
        return None
    clipped = shapely_shape(geometry).intersection(boundary)
    return json.loads(shapely.to_geojson(shapely.set_precision(clipped, 1e-5)))
