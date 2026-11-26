"""Dedza District facility-gap GIS web application."""

from __future__ import annotations

import heapq
import json
import math
import sqlite3
import warnings
from functools import lru_cache
from pathlib import Path

import duckdb
import numpy as np
import rasterio
from flask import Flask, Response, jsonify, request, send_from_directory
from rasterio.features import geometry_mask, rasterize
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.transform import Affine, from_bounds
from rasterio.windows import Window, from_bounds as window_from_bounds
from rasterio.warp import Resampling, reproject

from shapely import STRtree
from shapely.geometry import shape as shapely_shape

from scripts.planning_analysis import FLOOD_DEPTH_PATH, PERMANENT_WATER_PATH, POPULATION_PATHS
from scripts.site_constraints import point_in_geometry
from scripts.site_suitability import SiteChecker

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
ROUTE_DB_PATH = ROOT / "data" / "processed" / "clip.duckdb"
SITE_CONSTRAINT_DB_PATH = ROOT / "data" / "processed" / "site_constraints.sqlite"
ENVIRONMENTAL_RASTER_PATHS = {
    "land_cover": ROOT / "data" / "processed" / "environmental" / "land_cover.tif",
    "slope": ROOT / "data" / "processed" / "environmental" / "slope.tif",
    "flood": FLOOD_DEPTH_PATH,
}
STATIC = ROOT / "static"
ADM2_PATH = RAW / "MWI_ADM2_simplified.geojson"
# Traditional Authority boundaries: geoBoundaries gbHumanitarian (NSO Malawi / HDX, CC BY 3.0 IGO).
# The earlier gbOpen ADM3 file carried other districts' TA names on Dedza's polygons.
ADM3_PATH = RAW / "MWI_ADM3_humanitarian.geojson"
WORLDPOP_PATH = POPULATION_PATHS[2025]
WORLDPOP_2030_PATH = POPULATION_PATHS[2030]
# OSM classes counted as classified roads for site road access (as in build_db.py).
ACCESS_ROAD_CLASSES = (
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "primary_link", "secondary_link", "tertiary_link",
)

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")

SCENARIO_TRAVEL_TIME_BANDS_MIN = (30, 60)
MAX_ROAD_SNAP_KM = 1.0
MAX_SCENARIO_TRAVEL_TIME_MIN = max(SCENARIO_TRAVEL_TIME_BANDS_MIN)
_ROAD_NODE_INDEX: tuple[
    dict[tuple[float, float], tuple[float, float]],
    tuple[tuple[float, float], ...],
    np.ndarray,
] | None = None


class RouteNetworkUnavailable(Exception):
    pass


class RouteEndpointTooFar(Exception):
    pass


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12_742 * math.asin(math.sqrt(h))


def circle_polygon(lon: float, lat: float, radius_km: float, steps: int = 64) -> dict:
    earth_radius_km = 6371.0
    angular_radius = radius_km / earth_radius_km
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    ring = []
    for step in range(steps + 1):
        bearing = 2 * math.pi * step / steps
        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular_radius)
            + math.cos(lat1) * math.sin(angular_radius) * math.cos(bearing)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular_radius) * math.cos(lat1),
            math.cos(angular_radius) - math.sin(lat1) * math.sin(lat2),
        )
        ring.append([math.degrees(lon2), math.degrees(lat2)])
    return {"type": "Polygon", "coordinates": [ring]}


def motorcycle_speed_kmh(highway: str) -> float:
    if highway.endswith("_link"):
        highway = highway[:-5]
    return {
        "motorway": 40.0,
        "trunk": 40.0,
        "primary": 35.0,
        "secondary": 30.0,
        "tertiary": 25.0,
        "residential": 20.0,
        "unclassified": 20.0,
        "living_street": 20.0,
        "service": 10.0,
        "track": 10.0,
        "path": 10.0,
    }.get(highway, 15.0)


def motorcycle_speed_assumptions_kmh() -> dict[str, float]:
    return {
        "motorway/trunk": motorcycle_speed_kmh("motorway"),
        "primary": motorcycle_speed_kmh("primary"),
        "secondary": motorcycle_speed_kmh("secondary"),
        "tertiary": motorcycle_speed_kmh("tertiary"),
        "residential/unclassified/living_street": motorcycle_speed_kmh("residential"),
        "service/track/path": motorcycle_speed_kmh("service"),
        "other": motorcycle_speed_kmh("other"),
    }


def road_lines(geometry: dict) -> list[list[list[float]]]:
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        return [geometry.get("coordinates", [])]
    if geometry_type == "MultiLineString":
        return geometry.get("coordinates", [])
    if geometry_type == "GeometryCollection":
        return [
            line
            for part in geometry.get("geometries", [])
            for line in road_lines(part)
        ]
    return []


def project_road_node(node: tuple[float, float]) -> tuple[float, float]:
    lon, lat = node
    return (
        lon * 111_320 * math.cos(math.radians(lat)),
        lat * 110_540,
    )


@lru_cache(maxsize=1)
def load_road_network() -> tuple[
    dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
    dict[tuple[float, float], tuple[float, float]],
    dict[tuple[float, float], tuple[float, float]],
]:
    if not ROUTE_DB_PATH.exists():
        raise FileNotFoundError(
            f"Road network database missing at {ROUTE_DB_PATH}. "
            "Run: python scripts/build_db.py"
        )

    con = duckdb.connect(str(ROUTE_DB_PATH), read_only=True)
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        rows = con.execute(
            """
            WITH dedza AS (
                SELECT geom
                FROM ST_Read(?)
                WHERE lower(shapeName) = 'dedza'
            )
            SELECT lower(coalesce(CAST(r.highway AS VARCHAR), '')) AS highway,
                   lower(coalesce(CAST(r.oneway AS VARCHAR), '')) AS oneway,
                   ST_AsGeoJSON(ST_Intersection(r.geom, d.geom)) AS geojson
            FROM roads_clip r
            CROSS JOIN dedza d
            WHERE ST_Intersects(r.geom, d.geom)
              AND lower(coalesce(CAST(r.highway AS VARCHAR), '')) NOT IN ('footway', 'pedestrian')
            """,
            [str(ADM2_PATH)],
        ).fetchall()
    finally:
        con.close()

    graph: dict[
        tuple[float, float],
        list[tuple[tuple[float, float], float, float]],
    ] = {}
    coordinates: dict[tuple[float, float], tuple[float, float]] = {}
    for highway, oneway, geojson in rows:
        if not geojson:
            continue
        speed_kmh = motorcycle_speed_kmh(highway)
        geometry = json.loads(geojson)
        for line in road_lines(geometry):
            previous = None
            for coordinate in line:
                node = (round(float(coordinate[0]), 6), round(float(coordinate[1]), 6))
                coordinates[node] = node
                graph.setdefault(node, [])
                if previous is not None and previous != node:
                    distance = haversine_km(previous, node)
                    travel_time_min = distance / speed_kmh * 60
                    if oneway in ("yes", "true", "1"):
                        graph[previous].append((node, distance, travel_time_min))
                    elif oneway in ("-1", "reverse"):
                        graph[node].append((previous, distance, travel_time_min))
                    else:
                        graph[previous].append((node, distance, travel_time_min))
                        graph[node].append((previous, distance, travel_time_min))
                previous = node

    if not graph:
        raise RouteNetworkUnavailable("No mapped road segments are available in Dedza.")

    projected = {node: project_road_node(node) for node in graph}
    return graph, coordinates, projected


def nearest_road_node(
    point: tuple[float, float],
    projected: dict[tuple[float, float], tuple[float, float]],
) -> tuple[float, float]:
    global _ROAD_NODE_INDEX
    if _ROAD_NODE_INDEX is None or _ROAD_NODE_INDEX[0] is not projected:
        nodes = tuple(projected)
        node_coordinates = np.asarray([projected[node] for node in nodes], dtype=np.float64)
        _ROAD_NODE_INDEX = (projected, nodes, node_coordinates)

    point_xy = project_road_node(point)
    nodes = _ROAD_NODE_INDEX[1]
    node_coordinates = _ROAD_NODE_INDEX[2]
    differences = node_coordinates - point_xy
    nearest_index = int(np.argmin(np.einsum("ij,ij->i", differences, differences)))
    return nodes[nearest_index]


def shortest_road_path(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
    start: tuple[float, float],
    target: tuple[float, float],
    optimize_time: bool = False,
    banned_nodes: set[tuple[float, float]] | None = None,
    banned_edges: set[tuple[tuple[float, float], tuple[float, float]]] | None = None,
) -> list[tuple[float, float]] | None:
    banned_nodes = banned_nodes or set()
    banned_edges = banned_edges or set()
    if start in banned_nodes or target in banned_nodes:
        return None

    def edge_cost(distance_km: float, time_min: float) -> float:
        return time_min if optimize_time else distance_km

    def heuristic(node: tuple[float, float]) -> float:
        distance = haversine_km(node, target)
        return distance * 1.5 if optimize_time else distance

    costs = {start: 0.0}
    previous: dict[tuple[float, float], tuple[float, float]] = {}
    queue = [(heuristic(start), 0.0, start)]
    while queue:
        _, cost_so_far, current = heapq.heappop(queue)
        if cost_so_far > costs.get(current, float("inf")):
            continue
        if current == target:
            break
        for neighbor, distance_km, time_min in graph[current]:
            if neighbor in banned_nodes or (current, neighbor) in banned_edges:
                continue
            candidate = cost_so_far + edge_cost(distance_km, time_min)
            if candidate < costs.get(neighbor, float("inf")):
                costs[neighbor] = candidate
                previous[neighbor] = current
                heapq.heappush(
                    queue,
                    (candidate + heuristic(neighbor), candidate, neighbor),
                )

    if target not in costs:
        return None
    path = [target]
    while path[-1] != start:
        path.append(previous[path[-1]])
    return list(reversed(path))


def shortest_road_time_to_target(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
    start: tuple[float, float],
    target: tuple[float, float],
    max_time_min: float,
) -> float | None:
    if start not in graph or target not in graph:
        return None
    if start == target:
        return 0.0

    def heuristic(node: tuple[float, float]) -> float:
        return haversine_km(node, target) * 1.5

    costs = {start: 0.0}
    queue = [(heuristic(start), 0.0, start)]
    while queue:
        estimated_total, cost_so_far, current = heapq.heappop(queue)
        if estimated_total > max_time_min:
            break
        if cost_so_far > costs.get(current, float("inf")):
            continue
        if current == target:
            return cost_so_far
        for neighbor, _, time_min in graph[current]:
            candidate = cost_so_far + time_min
            if candidate > max_time_min or candidate >= costs.get(neighbor, float("inf")):
                continue
            estimated_candidate = candidate + heuristic(neighbor)
            if estimated_candidate <= max_time_min:
                costs[neighbor] = candidate
                heapq.heappush(queue, (estimated_candidate, candidate, neighbor))
    return None


_REVERSED_GRAPH: tuple[int, dict] | None = None


def reverse_road_graph(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
) -> dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]]:
    """Edges flipped (v -> u for every u -> v), cached for the current graph object."""
    global _REVERSED_GRAPH
    if _REVERSED_GRAPH is not None and _REVERSED_GRAPH[0] == id(graph):
        return _REVERSED_GRAPH[1]
    reversed_graph: dict = {node: [] for node in graph}
    for node, edges in graph.items():
        for neighbor, distance_km, time_min in edges:
            reversed_graph.setdefault(neighbor, []).append((node, distance_km, time_min))
    _REVERSED_GRAPH = (id(graph), reversed_graph)
    return reversed_graph


def road_times_to_sources(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
    sources: set[tuple[float, float]],
    max_time_min: float,
) -> dict[tuple[float, float], float]:
    """Travel time from every node to its nearest source node, within max_time_min.

    One multi-source Dijkstra on the reversed graph gives, for each node, the
    shortest time *to* the sources along the original (directed) roads. This
    replaces one search per settlement and returns exact shortest times.
    """
    reversed_graph = reverse_road_graph(graph)
    times = {source: 0.0 for source in sources if source in graph}
    queue = [(0.0, source) for source in times]
    heapq.heapify(queue)
    while queue:
        time_so_far, current = heapq.heappop(queue)
        if time_so_far > times.get(current, float("inf")):
            continue
        for neighbor, _, time_min in reversed_graph.get(current, ()):
            candidate = time_so_far + time_min
            if candidate > max_time_min or candidate >= times.get(neighbor, float("inf")):
                continue
            times[neighbor] = candidate
            heapq.heappush(queue, (candidate, neighbor))
    return times


def road_path_cost(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
    path: list[tuple[float, float]],
) -> float:
    cost = 0.0
    for current, neighbor in zip(path, path[1:]):
        cost += min(
            distance_km
            for next_node, distance_km, _ in graph[current]
            if next_node == neighbor
        )
    return cost


def alternative_road_paths(
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
    primary_path: list[tuple[float, float]],
    limit: int,
    probe_count: int = 4,
) -> list[list[tuple[float, float]]]:
    target = primary_path[-1]
    branch_indices = []
    for index, node in enumerate(primary_path[:-1]):
        previous_nodes = set(primary_path[:index])
        next_node = primary_path[index + 1]
        if any(
            neighbor not in previous_nodes and neighbor != next_node
            for neighbor, _, _ in graph[node]
        ):
            branch_indices.append(index)
    if not branch_indices or limit <= 0:
        return []

    sample_count = min(probe_count, len(branch_indices))
    if sample_count == 1:
        probe_indices = [branch_indices[len(branch_indices) // 2]]
    else:
        probe_indices = sorted({
            branch_indices[round(index * (len(branch_indices) - 1) / (sample_count - 1))]
            for index in range(sample_count)
        })

    candidates: dict[tuple[tuple[float, float], ...], list[tuple[float, float]]] = {}
    for index in probe_indices:
        root = primary_path[: index + 1]
        spur = shortest_road_path(
            graph,
            root[-1],
            target,
            banned_nodes=set(root[:-1]),
            banned_edges={(root[-1], primary_path[index + 1])},
        )
        if spur is None:
            continue
        candidate = root[:-1] + spur
        candidate_key = tuple(candidate)
        if candidate_key != tuple(primary_path):
            candidates[candidate_key] = candidate
    return sorted(
        candidates.values(),
        key=lambda path: road_path_cost(graph, path),
    )[:limit]


def route_option(
    path: list[tuple[float, float]],
    label: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
    start: tuple[float, float],
    target: tuple[float, float],
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]],
    coordinates: dict[tuple[float, float], tuple[float, float]],
    origin_snap_km: float,
    destination_snap_km: float,
) -> dict:
    route_coordinates = [list(coordinates[node]) for node in path]
    if len(route_coordinates) == 1:
        route_coordinates.append(route_coordinates[0])
    route_features = [
        {
            "type": "Feature",
            "properties": {"kind": "road"},
            "geometry": {"type": "LineString", "coordinates": route_coordinates},
        },
        {
            "type": "Feature",
            "properties": {"kind": "origin"},
            "geometry": {"type": "Point", "coordinates": list(origin)},
        },
        {
            "type": "Feature",
            "properties": {"kind": "destination"},
            "geometry": {"type": "Point", "coordinates": list(destination)},
        },
    ]
    if origin_snap_km > 0.001:
        route_features.append(
            {
                "type": "Feature",
                "properties": {"kind": "connector"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(origin), list(coordinates[start])],
                },
            }
        )
    if destination_snap_km > 0.001:
        route_features.append(
            {
                "type": "Feature",
                "properties": {"kind": "connector"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(coordinates[target]), list(destination)],
                },
            }
        )
    network_distance_km = road_path_cost(graph, path)
    estimated_network_time_min = sum(
        min(
            time_min
            for next_node, _, time_min in graph[current]
            if next_node == neighbor
        )
        for current, neighbor in zip(path, path[1:])
    )
    return {
        "label": label,
        "route": {"type": "FeatureCollection", "features": route_features},
        "network_distance_km": network_distance_km,
        "total_distance_km": network_distance_km + origin_snap_km + destination_snap_km,
        "estimated_network_time_min": estimated_network_time_min,
        "origin_snap_m": origin_snap_km * 1000,
        "destination_snap_m": destination_snap_km * 1000,
    }


def calculate_road_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> dict | None:
    graph, coordinates, projected = load_road_network()
    start = nearest_road_node(origin, projected)
    target = nearest_road_node(destination, projected)
    origin_snap_km = haversine_km(origin, coordinates[start])
    destination_snap_km = haversine_km(destination, coordinates[target])
    if max(origin_snap_km, destination_snap_km) > 1:
        raise RouteEndpointTooFar("An endpoint is more than 1 km from the mapped road network.")

    shortest_path = shortest_road_path(graph, start, target)
    if shortest_path is None:
        return None
    distance_paths = [
        shortest_path,
        *alternative_road_paths(graph, shortest_path, 1, probe_count=1),
    ]
    fastest_path = shortest_road_path(graph, start, target, optimize_time=True)
    path_labels: dict[tuple[tuple[float, float], ...], str] = {
        tuple(distance_paths[0]): "Shortest distance",
    }
    if fastest_path is not None:
        fastest_key = tuple(fastest_path)
        if fastest_key in path_labels:
            path_labels[fastest_key] += " and quickest estimated"
        else:
            path_labels[fastest_key] = "Quickest estimated"
    alternative_number = 0
    for path in distance_paths[1:]:
        path_key = tuple(path)
        if path_key not in path_labels and len(path_labels) < 3:
            alternative_number += 1
            path_labels[path_key] = f"Alternative {alternative_number}"

    options = [
        route_option(
            list(path),
            label,
            origin,
            destination,
            start,
            target,
            graph,
            coordinates,
            origin_snap_km,
            destination_snap_km,
        )
        for path, label in path_labels.items()
    ]
    return {**options[0], "alternatives": options}


def valid_lon_lat(value: object) -> tuple[float, float] | None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
    ):
        return None
    try:
        lon, lat = float(value[0]), float(value[1])
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lon, lat


def site_layer_status() -> dict:
    status = {
        "buildings": {
            "available": False,
            "count": 0,
            "source": None,
            "date": None,
            "license": None,
            "message": "Building footprints are not imported.",
        },
        "terrain": {
            "available": ENVIRONMENTAL_RASTER_PATHS["slope"].is_file(),
            "message": (
                "Slope is derived from Mapzen Terrain Tiles, a generalized terrain product "
                "based primarily on SRTM; use for preliminary screening only."
                if ENVIRONMENTAL_RASTER_PATHS["slope"].is_file()
                else "Terrain and slope are not installed. Run python scripts/import_environmental_layers.py."
            ),
        },
        "land_cover": {
            "available": ENVIRONMENTAL_RASTER_PATHS["land_cover"].is_file(),
            "message": (
                "ESA WorldCover 2021 v200 at 10 m resolution; land-cover classes are indicative, "
                "not parcel or wetland verification."
                if ENVIRONMENTAL_RASTER_PATHS["land_cover"].is_file()
                else "Land cover is not installed. Run python scripts/import_environmental_layers.py."
            ),
        },
        "flood": {
            "available": ENVIRONMENTAL_RASTER_PATHS["flood"].is_file(),
            "message": (
                "JRC Copernicus EMS GloFAS river flood hazard map v2.1: modelled water depth for a "
                "1-in-100-year flood at about 90 m. A global model, not an official national flood map."
                if ENVIRONMENTAL_RASTER_PATHS["flood"].is_file()
                else "Flood hazard is not installed. Run python scripts/import_planning_layers.py."
            ),
        },
    }
    if SITE_CONSTRAINT_DB_PATH.is_file():
        with sqlite3.connect(SITE_CONSTRAINT_DB_PATH) as con:
            metadata = dict(con.execute("SELECT key, value FROM metadata"))
            count = con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        status["buildings"] = {
            "available": True,
            "count": count,
            "source": metadata.get("buildings_source"),
            "date": metadata.get("buildings_date"),
            "license": metadata.get("buildings_license"),
            "message": (
                "Footprints are mapped detections and may be incomplete or out of date; "
                "no footprint does not prove land is vacant."
            ),
        }
    return status


def building_at(lon: float, lat: float) -> dict | None:
    if not SITE_CONSTRAINT_DB_PATH.is_file():
        return None
    with sqlite3.connect(SITE_CONSTRAINT_DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT id, height, confidence, geometry
            FROM buildings
            WHERE min_lon <= ? AND max_lon >= ?
              AND min_lat <= ? AND max_lat >= ?
            """,
            (lon, lon, lat, lat),
        ).fetchall()
    for row in rows:
        geometry = json.loads(row["geometry"])
        if point_in_geometry((lon, lat), geometry):
            return {
                "id": row["id"],
                "height": row["height"],
                "confidence": row["confidence"],
            }
    return None


def db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database missing at {DB_PATH}. Run: python scripts/build_db.py"
        )
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


@lru_cache(maxsize=1)
def load_payloads() -> dict:
    con = db()
    meta = {row["key"]: row["value"] for row in con.execute("SELECT key, value FROM meta")}
    boundary = json.loads(con.execute("SELECT geojson FROM boundary").fetchone()[0])
    facilities = [dict(r) for r in con.execute("SELECT * FROM facilities")]
    recs = [dict(r) for r in con.execute("SELECT * FROM recommendations ORDER BY rank")]
    tables = {
        row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    schools = [dict(r) for r in con.execute(
        "SELECT name, school_id, status, district, lon, lat FROM schools ORDER BY name"
    )] if "schools" in tables else []
    settlement_columns = {
        row[1] for row in con.execute("PRAGMA table_info(settlements)")
    } if "settlements" in tables else set()
    extra_columns = ", building_count, source" if "building_count" in settlement_columns else ""
    settlements = [dict(r) for r in con.execute(
        f"SELECT name, place, population, adm3_name, lon, lat{extra_columns} FROM settlements ORDER BY name"
    )] if "settlements" in tables else []
    registry = [dict(r) for r in con.execute(
        """SELECT source_id, facility_code, name, facility_type, ownership, lon, lat
           FROM mhfr_facilities ORDER BY name"""
    )] if "mhfr_facilities" in tables else []
    population_summary = [dict(r) for r in con.execute(
        """SELECT area_name, coverage, population_est, source, year
           FROM population_summary ORDER BY area_name, coverage"""
    )] if "population_summary" in tables else []

    cells = {"type": "FeatureCollection", "features": []}
    for r in con.execute("SELECT * FROM cells"):
        dist_km = (
            round(r["dist_facility_m"] / 1000, 2)
            if r["dist_facility_m"] is not None
            else None
        )
        cells["features"].append(
            {
                "type": "Feature",
                "properties": {
                    "cell_id": r["cell_id"],
                    "area_km2": round(r["area_km2"] or 0, 3),
                    "coverage": r["coverage"],
                    "dist_km": dist_km,
                    "road_km": round(r["road_km"] or 0, 2),
                    "water_km": round(r["water_km"] or 0, 2),
                    "facilities_in_cell": r["facilities_in_cell"],
                    "population_2020": round(r["population_2020"] or 0),
                    "population": round(r["population"] or 0),
                    "population_2030": round(r["population_2030"] or 0),
                    "growth": round(r["growth"] or 0),
                    "road_access_share": round(r["road_access_share"] or 0, 3),
                    "flood_area_share": round(r["flood_area_share"] or 0, 3),
                    "flood_pop_share": round(r["flood_pop_share"] or 0, 3),
                    "travel_min": None if r["travel_min"] is None else round(r["travel_min"], 1),
                    "road_access": r["road_access"],
                    "water_access": r["water_access"],
                    "priority_score": round(r["priority_score"] or 0, 3),
                },
                "geometry": json.loads(r["geojson"]),
            }
        )

    roads = {"type": "FeatureCollection", "features": []}
    for r in con.execute("SELECT highway, name, geojson FROM roads"):
        geom = json.loads(r["geojson"])
        roads["features"].append(
            {
                "type": "Feature",
                "properties": {"highway": r["highway"] or "", "name": r["name"] or ""},
                "geometry": geom,
            }
        )

    waterways = {"type": "FeatureCollection", "features": []}
    for r in con.execute("SELECT waterway, name, geojson FROM waterways"):
        waterways["features"].append(
            {
                "type": "Feature",
                "properties": {"waterway": r["waterway"] or "", "name": r["name"] or ""},
                "geometry": json.loads(r["geojson"]),
            }
        )

    type_counts = {
        r["facility_type"]: r["n"]
        for r in con.execute(
            "SELECT facility_type, COUNT(*) AS n FROM facilities GROUP BY facility_type"
        )
    }

    cov = {
        r["coverage"]: {"cells": r["n"], "km2": round(r["km2"], 1)}
        for r in con.execute(
            "SELECT coverage, COUNT(*) AS n, SUM(area_km2) AS km2 FROM cells GROUP BY coverage"
        )
    }
    con.close()
    return {
        "meta": meta,
        "type_counts": type_counts,
        "coverage": cov,
        "boundary": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Dedza District"},
                    "geometry": boundary,
                }
            ],
        },
        "facilities": facilities,
        "schools": schools,
        "settlements": settlements,
        "registry": registry,
        "population_summary": population_summary,
        "cells": cells,
        "roads": roads,
        "waterways": waterways,
        "recommendations": recs,
    }


@lru_cache(maxsize=1)
def site_checker() -> SiteChecker:
    """Flood, road, slope and land-cover checks built from the stored layers."""
    con = db()
    try:
        placeholders = ",".join("?" * len(ACCESS_ROAD_CLASSES))
        roads = [
            row[0]
            for row in con.execute(
                f"SELECT geojson FROM roads WHERE lower(highway) IN ({placeholders})",
                ACCESS_ROAD_CLASSES,
            )
        ]
    finally:
        con.close()
    return SiteChecker(
        roads,
        float(load_payloads()["meta"].get("road_access_km", 2)),
        FLOOD_DEPTH_PATH,
        PERMANENT_WATER_PATH,
        ENVIRONMENTAL_RASTER_PATHS["slope"],
        ENVIRONMENTAL_RASTER_PATHS["land_cover"],
    )


@lru_cache(maxsize=1)
def settlement_planning() -> list[dict]:
    """2030 population and site suitability for every settlement.

    A settlement's 2030 population applies the WorldPop 2025-2030 growth of the
    grid cell it lies in to its own 2025 estimate.
    """
    payloads = load_payloads()
    cells = payloads["cells"]["features"]
    polygons = [shapely_shape(feature["geometry"]) for feature in cells]
    tree = STRtree(polygons)
    meta = payloads["meta"]
    district_ratio = float(meta.get("population_2030", 1)) / max(float(meta.get("population_2025", 1)), 1)
    points = [(float(s["lon"]), float(s["lat"])) for s in payloads["settlements"]]
    checks = site_checker().check_many(points)
    result = []
    for settlement, point, check in zip(payloads["settlements"], points, checks):
        ratio = district_ratio
        for index in tree.query(shapely_shape({"type": "Point", "coordinates": point}), predicate="within"):
            props = cells[int(index)]["properties"]
            if props["population"] > 0:
                ratio = props["population_2030"] / props["population"]
            break
        population = float(settlement["population"] or 0)
        result.append({
            "population_2030": round(population * ratio, 1),
            "site_suitable": check["suitable"],
            "site_issues": [
                f'{c["label"]}: {c["detail"]}' for c in check["checks"] if c["status"] in ("fail", "warn")
            ],
        })
    return result


@lru_cache(maxsize=1)
def load_traditional_authorities() -> dict:
    if not ADM2_PATH.exists() or not ADM3_PATH.exists():
        raise FileNotFoundError("ADM2 and ADM3 boundary GeoJSON files are required.")

    con = duckdb.connect()
    con.execute("INSTALL spatial;")
    con.execute("LOAD spatial;")
    con.execute("SET geometry_always_xy = true;")
    rows = con.execute(
        """
        SELECT a.shapeName,
               ST_AsGeoJSON(ST_Intersection(a.geom, d.geom)) AS geojson
        FROM ST_Read(?) AS a
        CROSS JOIN ST_Read(?) AS d
        WHERE lower(d.shapeName) = 'dedza'
          AND ST_Intersects(a.geom, d.geom)
          AND ST_Area(ST_Transform(
                ST_Intersection(a.geom, d.geom), 'EPSG:4326', 'EPSG:32736'
          )) > 5000000
        ORDER BY a.shapeName
        """,
        [str(ADM3_PATH), str(ADM2_PATH)],
    ).fetchall()
    con.close()

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": name},
                "geometry": json.loads(geojson),
            }
            for name, geojson in rows
        ],
    }


@lru_cache(maxsize=2)
def scenario_baseline_road_times(
    facility_type: str,
) -> dict[tuple[str, float, float], tuple[tuple[float, float], float, float | None]]:
    payloads = load_payloads()
    graph, _, projected = load_road_network()
    existing_sites = payloads["schools"] if facility_type == "school" else payloads["facilities"]
    site_points = [(float(site["lon"]), float(site["lat"])) for site in existing_sites]
    site_nodes = [nearest_road_node(point, projected) for point in site_points]
    service_nodes = {
        node
        for point, node in zip(site_points, site_nodes)
        if haversine_km(point, node) <= MAX_ROAD_SNAP_KM
    }

    settlement_points = [
        (float(settlement["lon"]), float(settlement["lat"]))
        for settlement in payloads["settlements"]
    ]
    settlement_nodes = [nearest_road_node(point, projected) for point in settlement_points]
    # One search from all existing services instead of one per settlement.
    times_to_service = road_times_to_sources(graph, service_nodes, MAX_SCENARIO_TRAVEL_TIME_MIN)
    baselines = {}
    for settlement, point, node in zip(payloads["settlements"], settlement_points, settlement_nodes):
        snap_distance = haversine_km(point, node)
        baseline_time = times_to_service.get(node) if snap_distance <= MAX_ROAD_SNAP_KM else None
        key = (str(settlement["name"]), point[0], point[1])
        baselines[key] = (node, snap_distance, baseline_time)
    return baselines


def estimate_scenario_road_access(
    lon: float,
    lat: float,
    facility_type: str,
) -> dict:
    graph, _, projected = load_road_network()
    candidate_point = (lon, lat)
    candidate_node = nearest_road_node(candidate_point, projected)
    candidate_snap_distance = haversine_km(candidate_point, candidate_node)
    result = {
        "available": candidate_snap_distance <= MAX_ROAD_SNAP_KM,
        "time_bands_minutes": list(SCENARIO_TRAVEL_TIME_BANDS_MIN),
        "candidate_road_distance_km": round(candidate_snap_distance, 2),
        "speed_assumptions_kmh": motorcycle_speed_assumptions_kmh(),
        "connector_time_included": False,
        "capacity_assessment_available": False,
        "method": (
            "Road-network travel times from mapped settlements to the proposed site, compared with "
            "the nearest existing same-type baseline service. Straight-line access connectors are "
            "excluded; points more than 1 km from mapped roads are not assessed. Speeds are indicative "
            "motorcycle assumptions and do not account for traffic, road condition, stops, or season."
        ),
        "settlements": [],
    }
    if candidate_snap_distance > MAX_ROAD_SNAP_KM:
        result["reason"] = (
            f"The candidate is {candidate_snap_distance:.1f} km from the nearest mapped road; "
            f"road-time analysis is limited to sites within {MAX_ROAD_SNAP_KM:g} km of a mapped road."
        )
        return result

    payloads = load_payloads()
    baseline_times = scenario_baseline_road_times(facility_type)
    # One search from the candidate gives every settlement's time to it.
    times_to_candidate = road_times_to_sources(graph, {candidate_node}, MAX_SCENARIO_TRAVEL_TIME_MIN)
    for settlement in payloads["settlements"]:
        point = (float(settlement["lon"]), float(settlement["lat"]))
        key = (str(settlement["name"]), point[0], point[1])
        settlement_node, snap_distance, existing_time = baseline_times[key]
        candidate_time = times_to_candidate.get(settlement_node) if snap_distance <= MAX_ROAD_SNAP_KM else None
        result["settlements"].append({
            "name": settlement["name"],
            "adm3_name": settlement.get("adm3_name"),
            "coordinates": list(point),
            "road_accessible": snap_distance <= MAX_ROAD_SNAP_KM,
            "candidate_time_min": candidate_time,
            "existing_time_min": existing_time,
        })
    return result


def estimate_scenario_population(
    lon: float,
    lat: float,
    radius_km: float,
    facility_type: str,
) -> dict:
    boundary = load_payloads()["boundary"]
    boundary_geometries = [feature["geometry"] for feature in boundary["features"]]
    point_transform = Affine(1e-8, 0, lon - 0.5e-8, 0, -1e-8, lat + 0.5e-8)
    if not geometry_mask(
        boundary_geometries,
        out_shape=(1, 1),
        transform=point_transform,
        invert=True,
    )[0, 0]:
        raise ValueError("Candidate site must be inside Dedza District.")

    payloads = load_payloads()
    existing_sites = payloads["schools"] if facility_type == "school" else payloads["facilities"]
    nearby_sites = [
        site
        for site in existing_sites
        if haversine_km((lon, lat), (site["lon"], site["lat"])) <= 2 * radius_km + 0.1
    ]
    candidate_area = circle_polygon(lon, lat, radius_km)
    candidate_ring = candidate_area["coordinates"][0]
    left = min(point[0] for point in candidate_ring)
    bottom = min(point[1] for point in candidate_ring)
    right = max(point[0] for point in candidate_ring)
    top = max(point[1] for point in candidate_ring)
    ta_features = load_traditional_authorities()["features"]

    if not WORLDPOP_PATH.is_file():
        raise FileNotFoundError(f"WorldPop raster not found: {WORLDPOP_PATH}")

    with rasterio.open(WORLDPOP_PATH) as source:
        window = window_from_bounds(left, bottom, right, top, source.transform)
        window = window.round_offsets(op=math.floor).round_lengths(op=math.ceil).intersection(
            Window(0, 0, source.width, source.height)
        )
        population = source.read(1, window=window)
        transform = source.window_transform(window)
        # WorldPop's 2030 projection resampled onto the same 100 m pixels.
        population_2030 = np.full(population.shape, -1, dtype=np.float32)
        with rasterio.open(WORLDPOP_2030_PATH) as source_2030:
            reproject(
                source=rasterio.band(source_2030, 1),
                destination=population_2030,
                src_transform=source_2030.transform,
                src_crs=source_2030.crs,
                src_nodata=source_2030.nodata,
                dst_transform=transform,
                dst_crs=source.crs,
                dst_nodata=-1,
                resampling=Resampling.nearest,
            )
        population_2030 = np.where(np.isfinite(population_2030) & (population_2030 >= 0), population_2030, 0)
        inside_district = geometry_mask(
            boundary_geometries,
            out_shape=population.shape,
            transform=transform,
            invert=True,
        )
        candidate_mask = rasterize(
            [(candidate_area, 1)],
            out_shape=population.shape,
            transform=transform,
            fill=0,
            all_touched=False,
        ).astype(bool)
        existing_shapes = [
            (circle_polygon(site["lon"], site["lat"], radius_km), 1)
            for site in nearby_sites
        ]
        existing_mask = rasterize(
            existing_shapes,
            out_shape=population.shape,
            transform=transform,
            fill=0,
            all_touched=False,
        ).astype(bool) if existing_shapes else np.zeros(population.shape, dtype=bool)
        ta_codes = rasterize(
            [
                (feature["geometry"], index)
                for index, feature in enumerate(ta_features, start=1)
            ],
            out_shape=population.shape,
            transform=transform,
            fill=0,
            all_touched=False,
            dtype="uint16",
        )
        valid_population = np.isfinite(population) & (population >= 0)
        if source.nodata is not None:
            valid_population &= population != source.nodata
        candidate_population_mask = candidate_mask & inside_district & valid_population
        gained_population_mask = candidate_population_mask & ~existing_mask
        total_within_radius = float(population[candidate_population_mask].sum(dtype=np.float64))
        previously_covered = float(
            population[candidate_population_mask & existing_mask].sum(dtype=np.float64)
        )
        gained_population = float(population[gained_population_mask].sum(dtype=np.float64))
        total_within_radius_2030 = float(population_2030[candidate_population_mask].sum(dtype=np.float64))
        gained_population_2030 = float(population_2030[gained_population_mask].sum(dtype=np.float64))
        by_ta = []
        for index, feature in enumerate(ta_features, start=1):
            estimate = float(
                population[gained_population_mask & (ta_codes == index)].sum(dtype=np.float64)
            )
            if estimate > 0:
                by_ta.append({
                    "name": feature["properties"]["name"],
                    "population_estimate": round(estimate, 2),
                })
        assigned_population = sum(row["population_estimate"] for row in by_ta)
        outside_tas = round(max(0.0, gained_population - assigned_population), 2)
        if outside_tas > 0:
            by_ta.append({
                "name": "Outside mapped TA boundaries",
                "population_estimate": outside_tas,
            })

    return {
        "population_within_radius": round(total_within_radius, 2),
        "already_within_existing_service_radius": round(previously_covered, 2),
        "additional_population_estimate": round(gained_population, 2),
        "population_within_radius_2030": round(total_within_radius_2030, 2),
        "additional_population_2030": round(gained_population_2030, 2),
        "by_traditional_authority": sorted(
            by_ta,
            key=lambda row: (-row["population_estimate"], row["name"]),
        ),
        "method": (
            "WorldPop 2025 100 m population pixels inside the candidate's straight-line radius, "
            f"excluding pixels already inside an existing {'school' if facility_type == 'school' else 'baseline health-facility'} equal-radius zone. "
            "2030 figures use WorldPop's 2030 projection on the same pixels."
        ),
    }


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/health")
def health():
    database_ready = DB_PATH.exists()
    return jsonify(
        {
            "status": "ok" if database_ready else "missing_database",
            "database_ready": database_ready,
            "database_path": str(DB_PATH),
        }
    )


@app.get("/api/summary")
def summary():
    data = load_payloads()
    return jsonify(
        {
            "meta": data["meta"],
            "type_counts": data["type_counts"],
            "coverage": data["coverage"],
            "recommendations": data["recommendations"],
            "n_registry_facilities": len(data["registry"]),
        }
    )


@app.get("/api/gap-analysis")
def gap_analysis():
    try:
        served_km = float(request.args.get("served_km", 5))
        underserved_km = float(request.args.get("underserved_km", 8))
    except (TypeError, ValueError):
        return jsonify({"error": "Thresholds must be numbers."}), 400
    if (
        not math.isfinite(served_km)
        or not math.isfinite(underserved_km)
        or not 0 < served_km < underserved_km <= 50
    ):
        return jsonify(
            {"error": "Need 0 < served_km < underserved_km <= 50."}
        ), 400

    served_m = served_km * 1000
    underserved_m = underserved_km * 1000
    con = db()
    try:
        coverage_rows = con.execute(
            """
            WITH classified AS (
                SELECT area_km2,
                       CASE
                           WHEN dist_facility_m IS NULL THEN 'unserved'
                           WHEN dist_facility_m <= ? THEN 'served'
                           WHEN dist_facility_m <= ? THEN 'underserved'
                           ELSE 'unserved'
                       END AS coverage
                FROM cells
            )
            SELECT coverage, COUNT(*) AS cells, ROUND(SUM(area_km2), 1) AS km2
            FROM classified
            GROUP BY coverage
            """,
            (served_m, underserved_m),
        ).fetchall()
        cell_rows = con.execute(
            """
            SELECT cell_id,
                   CASE
                       WHEN dist_facility_m IS NULL THEN 'unserved'
                       WHEN dist_facility_m <= ? THEN 'served'
                       WHEN dist_facility_m <= ? THEN 'underserved'
                       ELSE 'unserved'
                   END AS coverage
            FROM cells
            ORDER BY cell_id
            """,
            (served_m, underserved_m),
        ).fetchall()
    finally:
        con.close()

    coverage = {
        "served": {"cells": 0, "km2": 0},
        "underserved": {"cells": 0, "km2": 0},
        "unserved": {"cells": 0, "km2": 0},
    }
    for row in coverage_rows:
        coverage[row["coverage"]] = {
            "cells": row["cells"],
            "km2": row["km2"] or 0,
        }
    return jsonify(
        {
            "served_km": served_km,
            "underserved_km": underserved_km,
            "coverage": coverage,
            "cells": [dict(row) for row in cell_rows],
        }
    )


PRIORITY_CRITERIA = ("population", "growth", "distance", "road", "flood")
# Matches MIN_RANKED_CELL_KM2 in scripts/build_db.py: border slivers are not ranked.
MIN_RANKED_CELL_KM2 = 1.0


def priority_baseline() -> tuple[dict[str, float], dict[str, float]]:
    """Baseline weights (percent) and score scales, as stored by scripts/build_db.py."""
    meta = load_payloads()["meta"]
    weights = {key: float(meta[f"weight_{key}"]) for key in PRIORITY_CRITERIA}
    scales = {
        "population_full": float(meta["population_full_score"]),
        "growth_full": float(meta["growth_full_score"]),
        "distance_full_m": float(meta["distance_full_m"]),
        "served_multiplier": float(meta["served_multiplier"]),
    }
    return weights, scales


@app.get("/api/priority-scenario")
def priority_scenario():
    """Re-score every grid cell in SQL with user-chosen criterion weights.

    Uses the same normalisation as the build-time score in scripts/build_db.py:
    WorldPop residents capped at the stored population_full_score, distance capped at 16 km and
    normalised to 8 km, road length capped at 8 km, mapped-waterway length capped
    at 4 km, and a 0.35 multiplier for served cells. Weights are normalised to
    sum to 1, so the stored baseline weights (meta table) reproduce the baseline.
    """
    baseline_weights, scales = priority_baseline()
    try:
        raw = {
            key: float(request.args.get(f"w_{key}", baseline_weights[key]))
            for key in PRIORITY_CRITERIA
        }
        served_km = float(request.args.get("served_km", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "Weights and served_km must be numbers."}), 400
    if not all(math.isfinite(v) and 0 <= v <= 100 for v in raw.values()):
        return jsonify({"error": "Each weight must be between 0 and 100."}), 400
    total = sum(raw.values())
    if total <= 0:
        return jsonify({"error": "At least one weight must be above zero."}), 400
    if not math.isfinite(served_km) or not 0 < served_km <= 50:
        return jsonify({"error": "Need 0 < served_km <= 50."}), 400

    weights = {key: value / total for key, value in raw.items()}
    con = db()
    try:
        rows = con.execute(
            """
            SELECT cell_id, area_km2,
                   ROUND((
                       MIN(population_2030 / :pop_full, 1.0) * :w_population
                       + MIN(MAX(growth, 0) / :growth_full, 1.0) * :w_growth
                       + MIN(COALESCE(dist_facility_m, :dist_full) / :dist_full, 1.0) * :w_distance
                       + road_access_share * :w_road
                       + (1 - flood_area_share) * :w_flood
                   ) * CASE
                           WHEN dist_facility_m IS NOT NULL AND dist_facility_m <= :served_m
                           THEN :served_multiplier ELSE 1.0
                       END, 6) AS score
            FROM cells
            ORDER BY score DESC, cell_id DESC
            """,
            {
                "pop_full": scales["population_full"],
                "growth_full": scales["growth_full"],
                "dist_full": scales["distance_full_m"],
                "served_multiplier": scales["served_multiplier"],
                **{f"w_{key}": value for key, value in weights.items()},
                "served_m": served_km * 1000,
            },
        ).fetchall()
    finally:
        con.close()

    return jsonify(
        {
            "weights": {key: round(value * 100, 1) for key, value in weights.items()},
            "baseline_weights": baseline_weights,
            "scales": scales,
            "served_km": served_km,
            "min_ranked_cell_km2": MIN_RANKED_CELL_KM2,
            "top_cell_ids": [
                row["cell_id"]
                for row in rows
                if (row["area_km2"] or 0) >= MIN_RANKED_CELL_KM2
            ][:12],
            "cells": [{"cell_id": row["cell_id"], "score": row["score"]} for row in rows],
        }
    )


@app.get("/api/traditional-authorities")
def traditional_authorities():
    return jsonify(load_traditional_authorities())


@app.get("/api/boundary")
def boundary():
    return jsonify(load_payloads()["boundary"])


@app.get("/api/site-layers")
def site_layers():
    return jsonify(site_layer_status())


@app.get("/api/environmental-tiles/<layer>/<int:z>/<int:x>/<int:y>.png")
def environmental_tile(layer: str, z: int, x: int, y: int):
    if layer not in ENVIRONMENTAL_RASTER_PATHS:
        return jsonify({"error": "Environmental layer must be land_cover, slope or flood."}), 404
    status_key = {"land_cover": "land_cover", "slope": "terrain", "flood": "flood"}[layer]
    status = site_layer_status()[status_key]
    if not status["available"]:
        return jsonify({"error": status["message"]}), 404
    if not 0 <= z <= 18:
        return jsonify({"error": "Zoom level must be between 0 and 18."}), 400
    tile_count = 1 << z
    if not (0 <= x < tile_count and 0 <= y < tile_count):
        return jsonify({"error": "Tile coordinates are outside the supported range."}), 400

    world = 2 * math.pi * 6_378_137.0
    west = x / tile_count * world - world / 2
    east = (x + 1) / tile_count * world - world / 2
    north = world / 2 - y / tile_count * world
    south = world / 2 - (y + 1) / tile_count * world
    tile_transform = from_bounds(west, south, east, north, 256, 256)
    source_path = ENVIRONMENTAL_RASTER_PATHS[layer]

    with rasterio.open(source_path) as source:
        nodata = source.nodata if source.nodata is not None else 0
        pixels = np.full((256, 256), nodata, dtype=np.float32)
        reproject(
            source=rasterio.band(source, 1),
            destination=pixels,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=tile_transform,
            dst_crs="EPSG:3857",
            dst_nodata=nodata,
            resampling=Resampling.nearest,
        )

    rgba = np.zeros((4, 256, 256), dtype=np.uint8)
    valid = np.isfinite(pixels) & (pixels != nodata)
    if layer == "land_cover":
        palette = {
            10: (38, 115, 45),
            20: (153, 191, 74),
            30: (192, 220, 130),
            40: (232, 198, 83),
            50: (202, 75, 64),
            60: (184, 164, 133),
            70: (235, 239, 240),
            80: (61, 144, 201),
            90: (76, 174, 143),
            95: (37, 112, 95),
            100: (123, 167, 91),
        }
        for value, color in palette.items():
            selected = valid & (pixels == value)
            rgba[:3, selected] = np.asarray(color, dtype=np.uint8)[:, None]
            rgba[3, selected] = 190
    elif layer == "flood":
        # Depth bands of the GloFAS categorised map: <1, 1-3, 3-10, >10 m.
        depth_colors = (
            (0, 1, (158, 202, 225)),
            (1, 3, (66, 146, 198)),
            (3, 10, (33, 102, 172)),
            (10, math.inf, (8, 48, 107)),
        )
        for lower, upper, color in depth_colors:
            selected = valid & (pixels > lower) & (pixels <= upper)
            rgba[:3, selected] = np.asarray(color, dtype=np.uint8)[:, None]
            rgba[3, selected] = 200
    else:
        slope_colors = (
            (0, 5, (65, 150, 88)),
            (5, 15, (207, 197, 93)),
            (15, 30, (221, 130, 65)),
            (30, math.inf, (171, 62, 56)),
        )
        for lower, upper, color in slope_colors:
            selected = valid & (pixels >= lower) & (pixels < upper)
            rgba[:3, selected] = np.asarray(color, dtype=np.uint8)[:, None]
            rgba[3, selected] = 190

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with MemoryFile() as memory_file:
            with memory_file.open(
                driver="PNG", width=256, height=256, count=4, dtype="uint8"
            ) as image:
                image.write(rgba)
            tile = memory_file.read()
    return Response(
        tile,
        mimetype="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/buildings")
def buildings():
    parts = request.args.get("bbox", "").split(",")
    if len(parts) != 4:
        return jsonify({"error": "bbox must be west,south,east,north."}), 400
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError:
        return jsonify({"error": "bbox coordinates must be numbers."}), 400
    if (
        not all(math.isfinite(value) for value in (west, south, east, north))
        or not (-180 <= west < east <= 180)
        or not (-90 <= south < north <= 90)
    ):
        return jsonify({"error": "bbox must be a valid geographic extent."}), 400

    status = site_layer_status()["buildings"]
    if not status["available"]:
        return jsonify({
            "type": "FeatureCollection",
            "features": [],
            "available": False,
            "message": status["message"],
        })

    limit = 2000
    with sqlite3.connect(SITE_CONSTRAINT_DB_PATH) as con:
        rows = con.execute(
            """
            SELECT id, height, confidence, geometry
            FROM buildings
            WHERE max_lon >= ? AND min_lon <= ?
              AND max_lat >= ? AND min_lat <= ?
            ORDER BY id
            LIMIT ?
            """,
            (west, east, south, north, limit + 1),
        ).fetchall()
    truncated = len(rows) > limit
    features = [
        {
            "type": "Feature",
            "properties": {
                "id": row[0],
                "height_m": row[1],
                "confidence": row[2],
            },
            "geometry": json.loads(row[3]),
        }
        for row in rows[:limit]
    ]
    return jsonify({
        "type": "FeatureCollection",
        "features": features,
        "available": True,
        "truncated": truncated,
        "source": status["source"],
    })


@app.post("/api/site-screening")
def site_screening():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON request body is required."}), 400
    coordinates = valid_lon_lat(data.get("coordinates"))
    if coordinates is None:
        return jsonify({"error": "coordinates must be a valid [longitude, latitude] pair."}), 400
    lon, lat = coordinates
    boundary_features = load_payloads()["boundary"]["features"]
    if not any(
        point_in_geometry(coordinates, feature["geometry"])
        for feature in boundary_features
    ):
        return jsonify({"error": "Candidate site must be inside Dedza District."}), 400

    layers = site_layer_status()
    building = building_at(lon, lat) if layers["buildings"]["available"] else None
    return jsonify({
        "buildings_available": layers["buildings"]["available"],
        "occupied": building is not None,
        "building": building,
        "building_message": layers["buildings"]["message"],
    })


@app.get("/api/facilities")
def facilities():
    return jsonify(load_payloads()["facilities"])


@app.post("/api/route")
def route():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Provide origin and destination coordinates."}), 400
    origin = valid_lon_lat(payload.get("origin"))
    destination = valid_lon_lat(payload.get("destination"))
    if origin is None or destination is None:
        return jsonify(
            {"error": "Origin and destination must be [longitude, latitude] coordinates."}
        ), 400
    try:
        result = calculate_road_route(origin, destination)
    except FileNotFoundError as error:
        return jsonify({"error": str(error)}), 503
    except RouteNetworkUnavailable as error:
        return jsonify({"error": str(error)}), 503
    except RouteEndpointTooFar as error:
        return jsonify({"error": str(error)}), 422
    if result is None:
        return jsonify(
            {
                "error": (
                    "No connected route was found. The mapped road network may be "
                    "incomplete between these locations."
                )
            }
        ), 422
    return jsonify(result)


@app.get("/api/registry-facilities")
def registry_facilities():
    """Operational Dedza facilities from the Malawi Health Facility Registry (MHFR)."""
    return jsonify(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": facility["name"],
                        "facility_type": facility["facility_type"],
                        "ownership": facility["ownership"],
                        "facility_code": facility["facility_code"],
                        "source_id": facility["source_id"],
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [facility["lon"], facility["lat"]],
                    },
                }
                for facility in load_payloads()["registry"]
            ],
        }
    )


@app.get("/api/population-summary")
def population_summary():
    return jsonify(load_payloads()["population_summary"])


@app.post("/api/scenario-population")
def scenario_population():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON request body is required."}), 400
    coordinates = valid_lon_lat(data.get("coordinates"))
    if coordinates is None:
        return jsonify({"error": "coordinates must be a valid [longitude, latitude] pair."}), 400
    facility_type = data.get("type")
    if facility_type not in ("school", "health"):
        return jsonify({"error": 'type must be "school" or "health".'}), 400
    radius = data.get("radius_km")
    if (
        isinstance(radius, bool)
        or not isinstance(radius, (int, float))
        or not 1 <= radius <= 50
        or not math.isfinite(radius)
    ):
        return jsonify({"error": "radius_km must be a number between 1 and 50."}), 400
    try:
        estimate = estimate_scenario_population(*coordinates, float(radius), facility_type)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except FileNotFoundError as error:
        return jsonify({"error": str(error)}), 503
    estimate["site_suitability"] = site_checker().check(*coordinates)
    try:
        estimate["road_time_access"] = estimate_scenario_road_access(
            *coordinates,
            facility_type,
        )
    except (FileNotFoundError, RouteNetworkUnavailable) as error:
        estimate["road_time_access"] = {
            "available": False,
            "reason": f"Road-time analysis unavailable: {error}",
            "capacity_assessment_available": False,
        }
    return jsonify(estimate)


@app.get("/api/schools")
def schools():
    return jsonify(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": school["name"] or "Unnamed school",
                        "school_id": school["school_id"],
                        "status": school["status"] or "",
                        "district": school["district"] or "",
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [school["lon"], school["lat"]],
                    },
                }
                for school in load_payloads()["schools"]
            ],
        }
    )


@app.get("/api/settlements")
def settlements():
    return jsonify(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": settlement["name"] or "Unnamed settlement",
                        "place": settlement["place"] or "",
                        "population": settlement["population"],
                        "adm3_name": settlement["adm3_name"] or "",
                        "building_count": settlement.get("building_count"),
                        "source": settlement.get("source") or "OpenStreetMap",
                        **planning,
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [settlement["lon"], settlement["lat"]],
                    },
                }
                for settlement, planning in zip(load_payloads()["settlements"], settlement_planning())
            ],
        }
    )


@app.get("/api/site-check")
def site_check():
    """Flood, road, slope and land-cover checks for one point."""
    try:
        coordinates = (float(request.args["lon"]), float(request.args["lat"]))
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lon and lat must be numbers."}), 400
    if valid_lon_lat(list(coordinates)) is None:
        return jsonify({"error": "lon and lat must be a valid coordinate."}), 400
    return jsonify(site_checker().check(*coordinates))


@app.get("/api/cells")
def cells():
    return jsonify(load_payloads()["cells"])


@app.get("/api/roads")
def roads():
    return jsonify(load_payloads()["roads"])


@app.get("/api/waterways")
def waterways():
    return jsonify(load_payloads()["waterways"])


@app.get("/api/recommendations")
def recommendations():
    return jsonify(load_payloads()["recommendations"])


def warm_caches() -> None:
    """Prepare the road network and scenario baselines so the first site comparison is fast."""
    try:
        load_road_network()
        for facility_type in ("health", "school"):
            scenario_baseline_road_times(facility_type)
        print("Road network and scenario baselines ready.", flush=True)
    except Exception as error:  # the app still works; the first comparison is just slower
        print(f"Cache warm-up skipped: {error}", flush=True)


if __name__ == "__main__":
    import threading

    threading.Thread(target=warm_caches, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
