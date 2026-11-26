"""Map layer endpoints: everything the map draws."""

from __future__ import annotations

import json
import math

from flask import Blueprint, Response, jsonify, request

from .. import config
from ..data import buildings_in_bbox, load_payloads, load_traditional_authorities, site_layer_status
from ..planning import settlement_planning
from ..routing import RouteNetworkUnavailable
from ..tiles import render_tile
from ..walking import health_walking_area

bp = Blueprint("layers", __name__, url_prefix="/api")


def _point_feature(properties: dict, lon: float, lat: float) -> dict:
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def _collection(features: list[dict]) -> Response:
    return jsonify({"type": "FeatureCollection", "features": features})


# ---------------------------------------------------------------------------
# Boundaries and grid
# ---------------------------------------------------------------------------

@bp.get("/boundary")
def boundary():
    return jsonify(load_payloads()["boundary"])


@bp.get("/traditional-authorities")
def traditional_authorities():
    return jsonify(load_traditional_authorities())


@bp.get("/cells")
def cells():
    return jsonify(load_payloads()["cells"])


@bp.get("/roads")
def roads():
    return jsonify(load_payloads()["roads"])


@bp.get("/waterways")
def waterways():
    return jsonify(load_payloads()["waterways"])


@bp.get("/recommendations")
def recommendations():
    return jsonify(load_payloads()["recommendations"])


# ---------------------------------------------------------------------------
# Services and settlements
# ---------------------------------------------------------------------------

@bp.get("/facilities")
def facilities():
    return jsonify(load_payloads()["facilities"])


@bp.get("/registry-facilities")
def registry_facilities():
    """Operational Dedza facilities from the Malawi Health Facility Registry (MHFR)."""
    return _collection([
        _point_feature(
            {
                "name": facility["name"],
                "facility_type": facility["facility_type"],
                "ownership": facility["ownership"],
                "facility_code": facility["facility_code"],
                "source_id": facility["source_id"],
            },
            facility["lon"],
            facility["lat"],
        )
        for facility in load_payloads()["registry"]
    ])


@bp.get("/schools")
def schools():
    return _collection([
        _point_feature(
            {
                "name": school["name"] or "Unnamed school",
                "school_id": school["school_id"],
                "status": school["status"] or "",
                "district": school["district"] or "",
            },
            school["lon"],
            school["lat"],
        )
        for school in load_payloads()["schools"]
    ])


@bp.get("/settlements")
def settlements():
    return _collection([
        _point_feature(
            {
                "name": settlement["name"] or "Unnamed settlement",
                "place": settlement["place"] or "",
                "population": settlement["population"],
                "adm3_name": settlement["adm3_name"] or "",
                "building_count": settlement.get("building_count"),
                "source": settlement.get("source") or "OpenStreetMap",
                **planning,
            },
            settlement["lon"],
            settlement["lat"],
        )
        for settlement, planning in zip(load_payloads()["settlements"], settlement_planning())
    ])


@bp.get("/health-walking-area")
def health_walking_area_api():
    limit_km = config.HEALTH_WALKING_AREA_KM
    try:
        geometry = health_walking_area(limit_km)
    except (FileNotFoundError, RouteNetworkUnavailable) as error:
        return jsonify({"error": f"Walking network unavailable: {error}"}), 503
    return jsonify({
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "limit_km": limit_km,
            "method": (
                f"Area within {limit_km:g} km walking distance of a baseline MOH health facility (including "
                "facilities within 20 km across the district border) along mapped roads, tracks and paths, "
                "sampled on a 250 m grid: a straight walk to the nearest path, then the shortest route."
            ),
        },
    })


# ---------------------------------------------------------------------------
# Site-constraint layers
# ---------------------------------------------------------------------------

@bp.get("/site-layers")
def site_layers():
    return jsonify(site_layer_status())


@bp.get("/environmental-tiles/<layer>/<int:z>/<int:x>/<int:y>.png")
def environmental_tile(layer: str, z: int, x: int, y: int):
    if layer not in config.ENVIRONMENTAL_RASTER_PATHS:
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

    tile = render_tile(layer, config.ENVIRONMENTAL_RASTER_PATHS[layer], z, x, y)
    return Response(tile, mimetype="image/png", headers={"Cache-Control": "public, max-age=3600"})


@bp.get("/buildings")
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
    rows = buildings_in_bbox(west, south, east, north, limit)
    features = [
        {
            "type": "Feature",
            "properties": {"id": row[0], "height_m": row[1], "confidence": row[2]},
            "geometry": json.loads(row[3]),
        }
        for row in rows[:limit]
    ]
    return jsonify({
        "type": "FeatureCollection",
        "features": features,
        "available": True,
        "truncated": len(rows) > limit,
        "source": status["source"],
    })
