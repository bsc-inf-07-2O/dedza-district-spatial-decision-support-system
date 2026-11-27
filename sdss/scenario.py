"""What a proposed school or health site would change.

- Population newly within walking distance (WorldPop pixels and settlements).
- Road travel time from settlements to the site versus the nearest existing service.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import rasterio
from rasterio.features import geometry_mask, rasterize
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject

from scripts.road_network import haversine_km

from . import config
from .data import load_payloads, load_traditional_authorities
from .routing import (
    load_road_network,
    speed_assumptions_kmh,
    nearest_road_node,
    road_times_to_sources,
)
from .walking import (
    service_walk_costs,
    settlement_nodes,
    walking_area_geometry,
    walking_index,
    walking_reach,
)


# ---------------------------------------------------------------------------
# Population reached on foot
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def district_population_pixels() -> dict:
    """Every populated WorldPop pixel in Dedza with its 2030 value, Traditional
    Authority and nearest walking-network node (computed once, then reused)."""
    from rasterio.mask import mask as raster_mask

    boundary = [feature["geometry"] for feature in load_payloads()["boundary"]["features"]]
    with rasterio.open(config.WORLDPOP_PATH) as source:
        clipped, transform = raster_mask(source, boundary, crop=True, filled=True, nodata=source.nodata)
        pop = clipped[0].astype(np.float64)
        nodata, crs = source.nodata, source.crs
    inside = geometry_mask(boundary, out_shape=pop.shape, transform=transform, invert=True)
    pop_2030 = np.full(pop.shape, -1, dtype=np.float32)
    with rasterio.open(config.WORLDPOP_2030_PATH) as source_2030:
        reproject(
            source=rasterio.band(source_2030, 1), destination=pop_2030,
            src_transform=source_2030.transform, src_crs=source_2030.crs, src_nodata=source_2030.nodata,
            dst_transform=transform, dst_crs=crs, dst_nodata=-1, resampling=Resampling.nearest,
        )
    ta_features = load_traditional_authorities()["features"]
    ta_codes = rasterize(
        [(feature["geometry"], i) for i, feature in enumerate(ta_features, start=1)],
        out_shape=pop.shape, transform=transform, fill=0, all_touched=False, dtype="uint16",
    )
    valid = inside & np.isfinite(pop) & (pop > 0) & (pop != nodata)
    rows, cols = np.nonzero(valid)
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e
    node_index, snap = walking_index().nearest_points(lon, lat)
    p30 = pop_2030[rows, cols].astype(np.float64)
    return {
        "lon": lon, "lat": lat,
        "pop": pop[rows, cols],
        "pop_2030": np.where(np.isfinite(p30) & (p30 >= 0), p30, 0),
        "ta": ta_codes[rows, cols],
        "ta_names": [feature["properties"]["name"] for feature in ta_features],
        "node": node_index, "snap": snap,
    }


def estimate_scenario_population(lon: float, lat: float, radius_km: float, facility_type: str) -> dict:
    """Residents within `radius_km` walking distance of a candidate site.

    Walking distance runs along roads, tracks and paths: a straight walk from the
    pixel (or settlement) to its nearest path node, then the shortest route. A
    resident counts as newly reached when the candidate is within the radius and
    no existing service of the same type is.
    """
    boundary_geometries = [feature["geometry"] for feature in load_payloads()["boundary"]["features"]]
    point_transform = Affine(1e-8, 0, lon - 0.5e-8, 0, -1e-8, lat + 0.5e-8)
    if not geometry_mask(boundary_geometries, out_shape=(1, 1), transform=point_transform, invert=True)[0, 0]:
        raise ValueError("Candidate site must be inside Dedza District.")
    if not config.WORLDPOP_PATH.is_file():
        raise FileNotFoundError(f"WorldPop raster not found: {config.WORLDPOP_PATH}")

    kind = "school" if facility_type == "school" else "health"
    reach, candidate_snap = walking_reach((lon, lat), radius_km)
    _, existing_costs, _ = service_walk_costs(kind)
    pixels = district_population_pixels()
    nodes = walking_index().nodes

    # Only pixels near the candidate can be within walking reach.
    margin = (radius_km + 1) / 110.574
    lon_margin = margin / max(math.cos(math.radians(lat)), 0.1)
    near = np.nonzero(
        (np.abs(pixels["lat"] - lat) <= margin) & (np.abs(pixels["lon"] - lon) <= lon_margin)
    )[0]
    to_candidate = np.array([reach.get(nodes[pixels["node"][i]], math.inf) + pixels["snap"][i] for i in near])
    to_existing = np.array([existing_costs.get(nodes[pixels["node"][i]], math.inf) + pixels["snap"][i] for i in near])
    within = near[to_candidate <= radius_km] if near.size else near
    covered = to_existing[to_candidate <= radius_km] <= radius_km if near.size else np.array([], bool)
    gained = within[~covered]
    already = within[covered]

    def total(indices, key="pop"):
        return float(pixels[key][indices].sum()) if len(indices) else 0.0

    by_ta = []
    for code, name in enumerate(pixels["ta_names"], start=1):
        estimate = total(gained[pixels["ta"][gained] == code])
        if estimate > 0:
            by_ta.append({"name": name, "population_estimate": round(estimate, 2)})
    outside_tas = round(max(0.0, total(gained) - sum(row["population_estimate"] for row in by_ta)), 2)
    if outside_tas > 0:
        by_ta.append({"name": "Outside mapped TA boundaries", "population_estimate": outside_tas})

    # Settlements newly within walking distance (for the settlement counts).
    reached_settlements = []
    for i, (node, snap) in enumerate(settlement_nodes()):
        distance = reach.get(node, math.inf) + snap
        if distance <= radius_km:
            reached_settlements.append({"index": i, "walk_km": round(distance, 2)})

    reach_geometry = walking_area_geometry(
        reach,
        radius_km,
        (lon - lon_margin, lat - margin, lon + lon_margin, lat + margin),
        max(0.1, radius_km / 50),
    )

    service_label = "school" if facility_type == "school" else "baseline health facility"
    return {
        "walking_reach_geojson": reach_geometry,
        "population_within_radius": round(total(within), 2),
        "already_within_existing_service_radius": round(total(already), 2),
        "additional_population_estimate": round(total(gained), 2),
        "population_within_radius_2030": round(total(within, "pop_2030"), 2),
        "additional_population_2030": round(total(gained, "pop_2030"), 2),
        "by_traditional_authority": sorted(by_ta, key=lambda row: (-row["population_estimate"], row["name"])),
        "reached_settlements": reached_settlements,
        "candidate_path_distance_km": round(candidate_snap, 2),
        "distance_method": "walking",
        "method": (
            f"WorldPop 2025 100 m population pixels within {radius_km:g} km walking distance of the candidate "
            "along mapped roads, tracks and paths, excluding pixels already within the same walking distance "
            f"of an existing {service_label}. 2030 figures use WorldPop's 2030 projection on the same pixels."
        ),
    }


# ---------------------------------------------------------------------------
# Road travel time to the site
# ---------------------------------------------------------------------------

TRAVEL_MODE_NAMES = {"vehicle": "motorcycle", "bicycle": "bicycle", "walking": "walking"}


@lru_cache(maxsize=6)
def scenario_baseline_road_times(
    facility_type: str,
    mode: str = "vehicle",
) -> dict[tuple[str, float, float], tuple[tuple[float, float], float, float | None]]:
    """Per settlement: nearest network node, snap distance and travel time (by
    `mode`) to the nearest existing service."""
    payloads = load_payloads()
    graph, _, projected = load_road_network(mode)
    existing_sites = payloads["schools"] if facility_type == "school" else payloads["facilities"]
    site_points = [(float(site["lon"]), float(site["lat"])) for site in existing_sites]
    site_nodes = [nearest_road_node(point, projected) for point in site_points]
    service_nodes = {
        node
        for point, node in zip(site_points, site_nodes)
        if haversine_km(point, node) <= config.MAX_ROAD_SNAP_KM
    }

    settlement_points = [
        (float(settlement["lon"]), float(settlement["lat"]))
        for settlement in payloads["settlements"]
    ]
    road_nodes = [nearest_road_node(point, projected) for point in settlement_points]
    # One search from all existing services instead of one per settlement.
    times_to_service = road_times_to_sources(graph, service_nodes, config.MAX_SCENARIO_TRAVEL_TIME_MIN)
    baselines = {}
    for settlement, point, node in zip(payloads["settlements"], settlement_points, road_nodes):
        snap_distance = haversine_km(point, node)
        baseline_time = times_to_service.get(node) if snap_distance <= config.MAX_ROAD_SNAP_KM else None
        key = (str(settlement["name"]), point[0], point[1])
        baselines[key] = (node, snap_distance, baseline_time)
    return baselines


def estimate_scenario_road_access(lon: float, lat: float, facility_type: str, mode: str = "vehicle") -> dict:
    """Travel time (by `mode`: "vehicle" motorcycle, "bicycle" or "walking")
    from each settlement to the candidate and to existing services."""
    graph, _, projected = load_road_network(mode)
    candidate_point = (lon, lat)
    candidate_node = nearest_road_node(candidate_point, projected)
    candidate_snap_distance = haversine_km(candidate_point, candidate_node)
    network = "roads" if mode == "vehicle" else "roads, tracks and paths"
    speeds = (
        "indicative motorcycle speeds by road class, without traffic, road condition, stops or season"
        if mode == "vehicle"
        else f"{TRAVEL_MODE_NAMES[mode]} at a steady {next(iter(speed_assumptions_kmh(mode).values())):g} km/h, "
        "without rests, hills or river crossings"
    )
    result = {
        "available": candidate_snap_distance <= config.MAX_ROAD_SNAP_KM,
        "mode": mode,
        "mode_label": TRAVEL_MODE_NAMES[mode],
        "time_bands_minutes": list(config.SCENARIO_TRAVEL_TIME_BANDS_MIN),
        "candidate_road_distance_km": round(candidate_snap_distance, 2),
        "speed_assumptions_kmh": speed_assumptions_kmh(mode),
        "connector_time_included": False,
        "capacity_assessment_available": False,
        "method": (
            f"Travel times by {TRAVEL_MODE_NAMES[mode]} along mapped {network} from mapped settlements to the "
            "proposed site, compared with the nearest existing same-type baseline service. Straight-line access "
            f"connectors are excluded; points more than 1 km from the mapped network are not assessed. Speeds: {speeds}."
        ),
        "settlements": [],
    }
    if candidate_snap_distance > config.MAX_ROAD_SNAP_KM:
        result["reason"] = (
            f"The candidate is {candidate_snap_distance:.1f} km from the nearest mapped {network}; "
            f"travel-time analysis is limited to sites within {config.MAX_ROAD_SNAP_KM:g} km of the network."
        )
        return result

    payloads = load_payloads()
    baseline_times = scenario_baseline_road_times(facility_type, mode)
    # One search from the candidate gives every settlement's time to it.
    times_to_candidate = road_times_to_sources(graph, {candidate_node}, config.MAX_SCENARIO_TRAVEL_TIME_MIN)
    for settlement in payloads["settlements"]:
        point = (float(settlement["lon"]), float(settlement["lat"]))
        key = (str(settlement["name"]), point[0], point[1])
        settlement_node, snap_distance, existing_time = baseline_times[key]
        candidate_time = (
            times_to_candidate.get(settlement_node) if snap_distance <= config.MAX_ROAD_SNAP_KM else None
        )
        result["settlements"].append({
            "name": settlement["name"],
            "adm3_name": settlement.get("adm3_name"),
            "coordinates": list(point),
            "road_accessible": snap_distance <= config.MAX_ROAD_SNAP_KM,
            "candidate_time_min": candidate_time,
            "existing_time_min": existing_time,
        })
    return result
