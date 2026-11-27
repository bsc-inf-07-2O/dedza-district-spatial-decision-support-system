"""Walking and vehicle networks built from the OpenStreetMap roads and paths.

* walking - every mapped way (roads, tracks, paths, footways) in both
  directions at WALKING_SPEED_KMH. Used for the 5 km access standard,
  coverage, population reach and site suggestions.
* vehicle - roads and tracks only (no paths or footways), one-way streets
  respected, indicative motorcycle speeds by road class. Used for travel
  time by motorcycle or ambulance.

Edges are (neighbour, distance_km, time_min). Nodes are (lon, lat) rounded to
6 decimals, so ways that share a vertex are connected.
"""

from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

import duckdb
import numpy as np

WALKING_SPEED_KMH = 5.0
# OSM classes a motor vehicle cannot use.
NON_VEHICLE_CLASSES = {"path", "footway", "pedestrian", "steps", "bridleway", "cycleway"}

Graph = dict[tuple[float, float], list[tuple[tuple[float, float], float, float]]]


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12_742 * math.asin(math.sqrt(h))


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


def road_lines(geometry: dict) -> list[list[list[float]]]:
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        return [geometry.get("coordinates", [])]
    if geometry_type == "MultiLineString":
        return geometry.get("coordinates", [])
    if geometry_type == "GeometryCollection":
        return [line for part in geometry.get("geometries", []) for line in road_lines(part)]
    return []


def project_km(lon: float, lat: float) -> tuple[float, float]:
    """Local planar coordinates in km (accurate to well under 1% across Dedza)."""
    return lon * 111.320 * math.cos(math.radians(lat)), lat * 110.574


def fetch_road_rows(route_db: Path) -> list[tuple[str, str, str]]:
    """(highway, oneway, geojson) for every mapped way intersecting Dedza.

    Ways are kept whole rather than cut at the district boundary, so routes can
    use the short stretches that cross into neighbouring districts.
    """
    if not route_db.exists():
        raise FileNotFoundError(f"Road network database missing at {route_db}. Run: python scripts/build_db.py")
    con = duckdb.connect(str(route_db), read_only=True)
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        return con.execute(
            """
            SELECT lower(coalesce(CAST(highway AS VARCHAR), '')),
                   lower(coalesce(CAST(oneway AS VARCHAR), '')),
                   ST_AsGeoJSON(geom)
            FROM roads_clip
            """
        ).fetchall()
    finally:
        con.close()


def build_graph(rows: list[tuple[str, str, str]], mode: str) -> Graph:
    if mode not in ("walking", "vehicle"):
        raise ValueError("mode must be walking or vehicle")
    graph: Graph = {}
    for highway, oneway, geojson in rows:
        if not geojson:
            continue
        if mode == "vehicle" and highway in NON_VEHICLE_CLASSES:
            continue
        speed = WALKING_SPEED_KMH if mode == "walking" else motorcycle_speed_kmh(highway)
        directed = mode == "vehicle"
        for line in road_lines(json.loads(geojson)):
            previous = None
            for coordinate in line:
                node = (round(float(coordinate[0]), 6), round(float(coordinate[1]), 6))
                graph.setdefault(node, [])
                if previous is not None and previous != node:
                    distance = haversine_km(previous, node)
                    minutes = distance / speed * 60
                    if directed and oneway in ("yes", "true", "1"):
                        graph[previous].append((node, distance, minutes))
                    elif directed and oneway in ("-1", "reverse"):
                        graph[node].append((previous, distance, minutes))
                    else:
                        graph[previous].append((node, distance, minutes))
                        graph[node].append((previous, distance, minutes))
                previous = node
    return graph


def reverse_graph(graph: Graph) -> Graph:
    reversed_graph: Graph = {node: [] for node in graph}
    for node, edges in graph.items():
        for neighbor, distance, minutes in edges:
            reversed_graph.setdefault(neighbor, []).append((node, distance, minutes))
    return reversed_graph


def shortest_costs(
    graph: Graph,
    sources: dict[tuple[float, float], float],
    weight: str = "km",
    limit: float = math.inf,
    labels: dict[tuple[float, float], int] | None = None,
) -> tuple[dict[tuple[float, float], float], dict[tuple[float, float], int]]:
    """Multi-source Dijkstra. `sources` maps start nodes to their starting cost
    (for example the walk from a facility to its nearest node). Returns the
    cost to every node reached within `limit`, and which source reached it.
    """
    index = 1 if weight == "km" else 2
    costs = dict(sources)
    origin = dict(labels or {})
    queue = [(cost, node) for node, cost in costs.items()]
    heapq.heapify(queue)
    while queue:
        cost, current = heapq.heappop(queue)
        if cost > costs.get(current, math.inf):
            continue
        for edge in graph.get(current, ()):
            candidate = cost + edge[index]
            neighbor = edge[0]
            if candidate <= limit and candidate < costs.get(neighbor, math.inf):
                costs[neighbor] = candidate
                if current in origin:
                    origin[neighbor] = origin[current]
                heapq.heappush(queue, (candidate, neighbor))
    return costs, origin


class NodeIndex:
    """Nearest-node lookup for one network, on local planar km coordinates."""

    def __init__(self, graph: Graph) -> None:
        self.nodes = tuple(graph)
        lon = np.fromiter((node[0] for node in self.nodes), dtype=np.float64, count=len(self.nodes))
        lat = np.fromiter((node[1] for node in self.nodes), dtype=np.float64, count=len(self.nodes))
        self.lon, self.lat = lon, lat
        self.x = lon * 111.320 * np.cos(np.radians(lat))
        self.y = lat * 110.574

    def nearest(self, point: tuple[float, float]) -> tuple[tuple[float, float], float]:
        x, y = project_km(*point)
        distances = (self.x - x) ** 2 + (self.y - y) ** 2
        i = int(np.argmin(distances))
        return self.nodes[i], haversine_km(point, self.nodes[i])

    def _buckets(self, cell_km: float) -> dict[tuple[int, int], np.ndarray]:
        cache = getattr(self, "_bucket_cache", {})
        if cell_km not in cache:
            bx = np.floor(self.x / cell_km).astype(np.int64)
            by = np.floor(self.y / cell_km).astype(np.int64)
            order = np.lexsort((by, bx))
            keys = np.stack([bx[order], by[order]], axis=1)
            breaks = np.nonzero(np.any(np.diff(keys, axis=0) != 0, axis=1))[0] + 1
            cache[cell_km] = {
                (int(group[0, 0]), int(group[0, 1])): indices
                for group, indices in zip(np.split(keys, breaks), np.split(order, breaks))
            }
            self._bucket_cache = cache
        return cache[cell_km]

    def nearest_points(
        self, lon: np.ndarray, lat: np.ndarray, cell_km: float = 0.5, max_km: float = math.inf
    ) -> tuple[np.ndarray, np.ndarray]:
        """Nearest node index and distance (km) for many points, using a grid of
        `cell_km` buckets. The search ring grows until the nearest node found is
        guaranteed to be the true nearest. With `max_km`, the search stops once
        it has covered that distance; points with no node within `max_km` get
        index -1 and distance inf."""
        buckets = self._buckets(cell_km)
        px = np.asarray(lon, dtype=np.float64) * 111.320 * np.cos(np.radians(lat))
        py = np.asarray(lat, dtype=np.float64) * 110.574
        bx = np.floor(px / cell_km).astype(np.int64)
        by = np.floor(py / cell_km).astype(np.int64)
        best = np.full(px.shape, -1, dtype=np.int64)
        best_d = np.full(px.shape, np.inf)
        groups: dict[tuple[int, int], list[int]] = {}
        for i, key in enumerate(zip(bx.tolist(), by.tolist())):
            groups.setdefault(key, []).append(i)
        for (gx, gy), members in groups.items():
            members = np.asarray(members)
            ring = 1
            while True:
                candidates = [
                    buckets[(gx + dx, gy + dy)]
                    for dx in range(-ring, ring + 1)
                    for dy in range(-ring, ring + 1)
                    if (gx + dx, gy + dy) in buckets
                ]
                capped = ring * cell_km >= max_km
                if candidates:
                    nodes = np.concatenate(candidates)
                    d = np.hypot(px[members, None] - self.x[nodes][None, :], py[members, None] - self.y[nodes][None, :])
                    arg = np.argmin(d, axis=1)
                    dist = d[np.arange(len(members)), arg]
                    # Any node outside the searched square is at least ring * cell_km away.
                    if dist.max() <= ring * cell_km or ring >= 40 or capped:
                        found = dist <= max_km
                        best[members[found]] = nodes[arg[found]]
                        best_d[members[found]] = dist[found]
                        break
                elif capped:
                    break
                ring *= 2
        return best, best_d
