"""Routing and candidate-site tools."""

from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

from .. import config
from ..data import building_at, inside_district, site_checker, site_layer_status
from ..geo import valid_lon_lat
from ..planning import suggest_sites
from ..routing import RouteEndpointTooFar, RouteNetworkUnavailable, calculate_road_route
from ..scenario import estimate_scenario_population, estimate_scenario_road_access

bp = Blueprint("sites", __name__, url_prefix="/api")

FACILITY_TYPES = ("school", "health")


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _valid_radius(radius: object) -> bool:
    return (
        not isinstance(radius, bool)
        and isinstance(radius, (int, float))
        and math.isfinite(radius)
        and 1 <= radius <= 50
    )


@bp.post("/route")
def route():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("Provide origin and destination coordinates.")
    origin = valid_lon_lat(payload.get("origin"))
    destination = valid_lon_lat(payload.get("destination"))
    if origin is None or destination is None:
        return _error("Origin and destination must be [longitude, latitude] coordinates.")
    mode = payload.get("mode", "vehicle")
    if mode not in config.ROUTE_MODES:
        return _error('mode must be "walking" or "vehicle".')
    try:
        result = calculate_road_route(origin, destination, mode)
    except (FileNotFoundError, RouteNetworkUnavailable) as error:
        return _error(str(error), 503)
    except RouteEndpointTooFar as error:
        return _error(str(error), 422)
    if result is None:
        return _error(
            "No connected route was found. The mapped road network may be "
            "incomplete between these locations.",
            422,
        )
    return jsonify(result)


@bp.get("/site-check")
def site_check():
    """Flood, road, slope and land-cover checks for one point."""
    try:
        coordinates = (float(request.args["lon"]), float(request.args["lat"]))
    except (KeyError, TypeError, ValueError):
        return _error("lon and lat must be numbers.")
    if valid_lon_lat(list(coordinates)) is None:
        return _error("lon and lat must be a valid coordinate.")
    return jsonify(site_checker().check(*coordinates))


@bp.post("/site-screening")
def site_screening():
    """Whether a candidate point falls on a detected building footprint."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("A JSON request body is required.")
    coordinates = valid_lon_lat(data.get("coordinates"))
    if coordinates is None:
        return _error("coordinates must be a valid [longitude, latitude] pair.")
    if not inside_district(coordinates):
        return _error("Candidate site must be inside Dedza District.")

    layers = site_layer_status()
    building = building_at(*coordinates) if layers["buildings"]["available"] else None
    return jsonify({
        "buildings_available": layers["buildings"]["available"],
        "occupied": building is not None,
        "building": building,
        "building_message": layers["buildings"]["message"],
    })


@bp.post("/scenario-population")
def scenario_population():
    """Population reached, site checks and road-time access for a proposed site."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("A JSON request body is required.")
    coordinates = valid_lon_lat(data.get("coordinates"))
    if coordinates is None:
        return _error("coordinates must be a valid [longitude, latitude] pair.")
    facility_type = data.get("type")
    if facility_type not in FACILITY_TYPES:
        return _error('type must be "school" or "health".')
    radius = data.get("radius_km")
    if not _valid_radius(radius):
        return _error("radius_km must be a number between 1 and 50.")
    try:
        estimate = estimate_scenario_population(*coordinates, float(radius), facility_type)
    except ValueError as error:
        return _error(str(error))
    except FileNotFoundError as error:
        return _error(str(error), 503)
    estimate["site_suitability"] = site_checker().check(*coordinates)
    try:
        estimate["road_time_access"] = estimate_scenario_road_access(*coordinates, facility_type)
    except (FileNotFoundError, RouteNetworkUnavailable) as error:
        estimate["road_time_access"] = {
            "available": False,
            "reason": f"Road-time analysis unavailable: {error}",
            "capacity_assessment_available": False,
        }
    return jsonify(estimate)


@bp.post("/suggest-sites")
def suggested_sites():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("A JSON request body is required.")
    facility_type = data.get("type")
    if facility_type not in FACILITY_TYPES:
        return _error('type must be "school" or "health".')
    radius = data.get("radius_km")
    if not _valid_radius(radius):
        return _error("radius_km must be a number between 1 and 50.")
    exclude = [valid_lon_lat(point) for point in data.get("exclude") or []]
    if any(point is None for point in exclude):
        return _error("exclude must be a list of [longitude, latitude] pairs.")
    count = data.get("count", 3)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
        return _error("count must be an integer between 1 and 10.")
    try:
        picks = suggest_sites(facility_type, float(radius), data.get("area") or None, exclude, count)
    except ValueError as error:
        return _error(str(error))
    return jsonify({
        "picks": picks,
        "method": (
            "Greedy maximal coverage: each pick is the suitable settlement location that brings the most "
            f"projected 2030 residents of unserved settlements within {float(radius):g} km walking distance."
        ),
    })
