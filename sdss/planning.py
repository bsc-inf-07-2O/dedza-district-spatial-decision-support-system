"""Settlement planning figures, priority scoring inputs and suggested sites."""

from __future__ import annotations

import heapq
import math
from functools import lru_cache

import numpy as np
from shapely import STRtree
from shapely.geometry import shape as shapely_shape

from scripts.site_constraints import point_in_geometry

from . import config
from .data import load_payloads, load_traditional_authorities, site_checker
from .schools import SCHOOL_LEVELS, service_kind
from .walking import service_walk_costs, settlement_nodes, walking_reach


@lru_cache(maxsize=1)
def settlement_planning() -> list[dict]:
    """2030 population, walking distance to services and site suitability for every settlement.

    A settlement's 2030 population applies the WorldPop 2025-2030 growth of the
    grid cell it lies in to its own 2025 estimate.
    """
    payloads = load_payloads()
    cells = payloads["cells"]["features"]
    tree = STRtree([shapely_shape(feature["geometry"]) for feature in cells])
    meta = payloads["meta"]
    district_ratio = float(meta.get("population_2030", 1)) / max(float(meta.get("population_2025", 1)), 1)
    points = [(float(s["lon"]), float(s["lat"])) for s in payloads["settlements"]]
    checks = site_checker().check_many(points)
    nodes = settlement_nodes()
    health = service_walk_costs("health")
    # Walking distance to the nearest school of any level, and of each level.
    school_kinds = {level: service_walk_costs(service_kind("school", level)) for level in SCHOOL_LEVELS}

    def nearest_service(node, snap, service):
        sites, costs, origin = service
        if node not in costs:
            return None, None
        return round(costs[node] + snap, 2), sites[origin[node]]

    result = []
    for settlement, point, check, (node, snap) in zip(payloads["settlements"], points, checks, nodes):
        walk_health, nearest_health = nearest_service(node, snap, health)
        schools = {}
        for level, service in school_kinds.items():
            walk_school, school = nearest_service(node, snap, service)
            suffix = "" if level == "all" else f"_{level}"
            schools[f"walk_km_school{suffix}"] = walk_school
            schools[f"nearest_school{suffix}_index"] = None if school is None else school["index"]
        ratio = district_ratio
        for index in tree.query(shapely_shape({"type": "Point", "coordinates": point}), predicate="within"):
            props = cells[int(index)]["properties"]
            if props["population"] > 0:
                ratio = props["population_2030"] / props["population"]
            break
        population = float(settlement["population"] or 0)
        result.append({
            "walk_km_health": walk_health,
            "nearest_health": nearest_health,
            **schools,
            "population_2030": round(population * ratio, 1),
            "site_suitable": check["suitable"],
            "site_issues": [
                f'{c["label"]}: {c["detail"]}' for c in check["checks"] if c["status"] in ("fail", "warn")
            ],
        })
    return result


def priority_baseline() -> tuple[dict[str, float], dict[str, float]]:
    """Baseline weights (percent) and score scales, as stored by scripts/build_db.py."""
    meta = load_payloads()["meta"]
    weights = {key: float(meta[f"weight_{key}"]) for key in config.PRIORITY_CRITERIA}
    scales = {
        "population_full": float(meta["population_full_score"]),
        "growth_full": float(meta["growth_full_score"]),
        "distance_full_m": float(meta["distance_full_m"]),
        "served_multiplier": float(meta["served_multiplier"]),
        "full_score_percentile": float(meta.get("full_score_percentile", 0.75)),
    }
    return weights, scales


def suggest_sites(
    facility_type: str,
    radius_km: float,
    area: str | None,
    exclude: list,
    count: int = 3,
    school_level: str = "all",
) -> list[dict]:
    """Greedy maximal coverage on walking distance.

    Demand: settlements in scope whose walk to the nearest existing service is
    longer than the radius, weighted by projected 2030 residents. For schools,
    `school_level` ("all", "primary" or "secondary") picks which existing
    schools count as the service. Candidate
    sites: settlements in scope that pass the site checks. Straight-line reach
    is an upper bound of walking reach, so candidates are evaluated lazily
    (exact walking reach only for the most promising ones).
    """
    settlements = load_payloads()["settlements"]
    planning = settlement_planning()
    nodes = settlement_nodes()
    if facility_type != "school":
        key = "walk_km_health"
    else:
        key = "walk_km_school" if school_level == "all" else f"walk_km_school_{school_level}"
    scope = None
    if area:
        scope = next(
            (f["geometry"] for f in load_traditional_authorities()["features"] if f["properties"]["name"] == area),
            None,
        )
        if scope is None:
            raise ValueError(f"Unknown area: {area}")
    in_scope = [
        scope is None or point_in_geometry((float(s["lon"]), float(s["lat"])), scope) for s in settlements
    ]
    excluded_reach = [walking_reach(tuple(point), radius_km) for point in exclude]

    def reached_by_excluded(i: int) -> bool:
        node, snap = nodes[i]
        return any(costs.get(node, math.inf) + snap <= radius_km for costs, _ in excluded_reach)

    weight = {
        i: planning[i]["population_2030"]
        for i in range(len(settlements))
        if in_scope[i]
        and planning[i]["population_2030"] > 0
        and (planning[i][key] is None or planning[i][key] > radius_km)
        and not reached_by_excluded(i)
    }
    if not weight:
        return []
    demand = np.array(sorted(weight))
    lon = np.array([float(settlements[i]["lon"]) for i in demand])
    lat = np.array([float(settlements[i]["lat"]) for i in demand])
    pop = np.array([weight[i] for i in demand])
    candidates = [i for i in range(len(settlements)) if in_scope[i] and planning[i]["site_suitable"]]

    # Heap entries: (-gain, candidate, number of picks when the gain was computed; -1 = upper bound).
    heap = []
    for c in candidates:
        cx, cy = float(settlements[c]["lon"]), float(settlements[c]["lat"])
        straight = np.hypot((lon - cx) * 111.32 * math.cos(math.radians(cy)), (lat - cy) * 110.574)
        bound = float(pop[straight <= radius_km].sum())
        if bound > 0:
            heap.append((-bound, c, -1))
    heapq.heapify(heap)
    reach_cache: dict[int, set[int]] = {}
    covered: set[int] = set()
    picks = []
    while heap and len(picks) < count:
        negative, c, evaluated_at = heapq.heappop(heap)
        if evaluated_at == len(picks):
            if -negative <= 0:
                break
            newly = reach_cache[c] - covered
            covered |= newly
            picks.append({
                "index": c,
                "name": settlements[c]["name"],
                "adm3_name": settlements[c].get("adm3_name"),
                "coordinates": [float(settlements[c]["lon"]), float(settlements[c]["lat"])],
                "people": round(sum(weight[j] for j in newly), 1),
                "settlement_count": len(newly),
            })
            continue
        if c not in reach_cache:
            costs, _ = walking_reach((float(settlements[c]["lon"]), float(settlements[c]["lat"])), radius_km)
            reach_cache[c] = {
                int(j) for j in demand
                if costs.get(nodes[j][0], math.inf) + nodes[j][1] <= radius_km
            }
        gain = sum(weight[j] for j in reach_cache[c] - covered)
        heapq.heappush(heap, (-gain, c, len(picks)))
    return picks
