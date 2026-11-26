"""Road network graph, shortest paths and route options.

A graph maps each node (lon, lat) to its outgoing edges as
``(neighbor, distance_km, time_min)``.
"""

from __future__ import annotations

import heapq
import math
from functools import lru_cache

import numpy as np

from scripts.road_network import build_graph, fetch_road_rows, haversine_km, motorcycle_speed_kmh

from . import config

Node = tuple[float, float]
Graph = dict[Node, list[tuple[Node, float, float]]]

# Nearest-node lookup arrays, one per network (keyed by the projected-node dict).
_ROAD_NODE_INDEX: dict[int, tuple[tuple[Node, ...], np.ndarray]] = {}
_REVERSED_GRAPH: tuple[int, Graph] | None = None


class RouteNetworkUnavailable(Exception):
    pass


class RouteEndpointTooFar(Exception):
    pass


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


# ---------------------------------------------------------------------------
# Loading the network
# ---------------------------------------------------------------------------

def project_road_node(node: Node) -> tuple[float, float]:
    """Approximate metres east/north, for fast nearest-node search."""
    lon, lat = node
    return (
        lon * 111_320 * math.cos(math.radians(lat)),
        lat * 110_540,
    )


@lru_cache(maxsize=1)
def road_rows() -> list[tuple[str, str, str]]:
    return fetch_road_rows(config.ROUTE_DB_PATH)


@lru_cache(maxsize=2)
def load_road_network(mode: str = "vehicle") -> tuple[Graph, dict[Node, Node], dict[Node, tuple[float, float]]]:
    """Road graph for `mode`: "vehicle" (roads and tracks, motorcycle speeds,
    one-way respected) or "walking" (all ways including paths, 5 km/h)."""
    graph = build_graph(road_rows(), mode)
    if not graph:
        raise RouteNetworkUnavailable("No mapped road segments are available in Dedza.")
    coordinates = {node: node for node in graph}
    projected = {node: project_road_node(node) for node in graph}
    return graph, coordinates, projected


def nearest_road_node(point: Node, projected: dict[Node, tuple[float, float]]) -> Node:
    key = id(projected)
    if key not in _ROAD_NODE_INDEX:
        nodes = tuple(projected)
        _ROAD_NODE_INDEX[key] = (nodes, np.asarray([projected[node] for node in nodes], dtype=np.float64))
    nodes, node_coordinates = _ROAD_NODE_INDEX[key]
    differences = node_coordinates - project_road_node(point)
    return nodes[int(np.argmin(np.einsum("ij,ij->i", differences, differences)))]


# ---------------------------------------------------------------------------
# Shortest paths and travel times
# ---------------------------------------------------------------------------

def shortest_road_path(
    graph: Graph,
    start: Node,
    target: Node,
    optimize_time: bool = False,
    banned_nodes: set[Node] | None = None,
    banned_edges: set[tuple[Node, Node]] | None = None,
) -> list[Node] | None:
    """A* path by distance (or by time with `optimize_time`)."""
    banned_nodes = banned_nodes or set()
    banned_edges = banned_edges or set()
    if start in banned_nodes or target in banned_nodes:
        return None

    def edge_cost(distance_km: float, time_min: float) -> float:
        return time_min if optimize_time else distance_km

    def heuristic(node: Node) -> float:
        distance = haversine_km(node, target)
        return distance * 1.5 if optimize_time else distance

    costs = {start: 0.0}
    previous: dict[Node, Node] = {}
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
                heapq.heappush(queue, (candidate + heuristic(neighbor), candidate, neighbor))

    if target not in costs:
        return None
    path = [target]
    while path[-1] != start:
        path.append(previous[path[-1]])
    return list(reversed(path))


def shortest_road_time_to_target(graph: Graph, start: Node, target: Node, max_time_min: float) -> float | None:
    """Quickest time from `start` to `target`, or None beyond `max_time_min`."""
    if start not in graph or target not in graph:
        return None
    if start == target:
        return 0.0

    def heuristic(node: Node) -> float:
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


def reverse_road_graph(graph: Graph) -> Graph:
    """Edges flipped (v -> u for every u -> v), cached for the current graph object."""
    global _REVERSED_GRAPH
    if _REVERSED_GRAPH is not None and _REVERSED_GRAPH[0] == id(graph):
        return _REVERSED_GRAPH[1]
    reversed_graph: Graph = {node: [] for node in graph}
    for node, edges in graph.items():
        for neighbor, distance_km, time_min in edges:
            reversed_graph.setdefault(neighbor, []).append((node, distance_km, time_min))
    _REVERSED_GRAPH = (id(graph), reversed_graph)
    return reversed_graph


def road_times_to_sources(graph: Graph, sources: set[Node], max_time_min: float) -> dict[Node, float]:
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


def road_path_cost(graph: Graph, path: list[Node]) -> float:
    """Length of `path` in km."""
    cost = 0.0
    for current, neighbor in zip(path, path[1:]):
        cost += min(distance_km for next_node, distance_km, _ in graph[current] if next_node == neighbor)
    return cost


def _road_path_time(graph: Graph, path: list[Node]) -> float:
    return sum(
        min(time_min for next_node, _, time_min in graph[current] if next_node == neighbor)
        for current, neighbor in zip(path, path[1:])
    )


def alternative_road_paths(graph: Graph, primary_path: list[Node], limit: int, probe_count: int = 4) -> list[list[Node]]:
    """Up to `limit` different paths that branch off `primary_path` (spur paths)."""
    target = primary_path[-1]
    branch_indices = []
    for index, node in enumerate(primary_path[:-1]):
        previous_nodes = set(primary_path[:index])
        next_node = primary_path[index + 1]
        if any(neighbor not in previous_nodes and neighbor != next_node for neighbor, _, _ in graph[node]):
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

    candidates: dict[tuple[Node, ...], list[Node]] = {}
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
        if tuple(candidate) != tuple(primary_path):
            candidates[tuple(candidate)] = candidate
    return sorted(candidates.values(), key=lambda path: road_path_cost(graph, path))[:limit]


# ---------------------------------------------------------------------------
# Route between two points (the Route tool)
# ---------------------------------------------------------------------------

def _line_feature(kind: str, coordinates: list) -> dict:
    return {"type": "Feature", "properties": {"kind": kind}, "geometry": {"type": "LineString", "coordinates": coordinates}}


def _point_feature(kind: str, point: Node) -> dict:
    return {"type": "Feature", "properties": {"kind": kind}, "geometry": {"type": "Point", "coordinates": list(point)}}


def route_option(
    path: list[Node],
    label: str,
    origin: Node,
    destination: Node,
    start: Node,
    target: Node,
    graph: Graph,
    coordinates: dict[Node, Node],
    origin_snap_km: float,
    destination_snap_km: float,
) -> dict:
    """One route choice as GeoJSON plus its distance and estimated time."""
    route_coordinates = [list(coordinates[node]) for node in path]
    if len(route_coordinates) == 1:
        route_coordinates.append(route_coordinates[0])
    features = [
        _line_feature("road", route_coordinates),
        _point_feature("origin", origin),
        _point_feature("destination", destination),
    ]
    if origin_snap_km > 0.001:
        features.append(_line_feature("connector", [list(origin), list(coordinates[start])]))
    if destination_snap_km > 0.001:
        features.append(_line_feature("connector", [list(coordinates[target]), list(destination)]))
    network_distance_km = road_path_cost(graph, path)
    return {
        "label": label,
        "route": {"type": "FeatureCollection", "features": features},
        "network_distance_km": network_distance_km,
        "total_distance_km": network_distance_km + origin_snap_km + destination_snap_km,
        "estimated_network_time_min": _road_path_time(graph, path),
        "origin_snap_m": origin_snap_km * 1000,
        "destination_snap_m": destination_snap_km * 1000,
    }


def calculate_road_route(origin: Node, destination: Node, mode: str = "vehicle") -> dict | None:
    """Shortest, quickest and alternative routes between two points."""
    graph, coordinates, projected = load_road_network(mode)
    start = nearest_road_node(origin, projected)
    target = nearest_road_node(destination, projected)
    origin_snap_km = haversine_km(origin, coordinates[start])
    destination_snap_km = haversine_km(destination, coordinates[target])
    if max(origin_snap_km, destination_snap_km) > 1:
        network = "path or road" if mode == "walking" else "road"
        raise RouteEndpointTooFar(f"An endpoint is more than 1 km from the mapped {network} network.")

    shortest_path = shortest_road_path(graph, start, target)
    if shortest_path is None:
        return None
    distance_paths = [shortest_path, *alternative_road_paths(graph, shortest_path, 1, probe_count=1)]
    # On foot every way has the same speed, so the shortest route is also the quickest.
    fastest_path = None if mode == "walking" else shortest_road_path(graph, start, target, optimize_time=True)
    path_labels: dict[tuple[Node, ...], str] = {
        tuple(distance_paths[0]): "Shortest walking route" if mode == "walking" else "Shortest distance",
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
            list(path), label, origin, destination, start, target,
            graph, coordinates, origin_snap_km, destination_snap_km,
        )
        for path, label in path_labels.items()
    ]
    return {**options[0], "alternatives": options, "mode": mode}
