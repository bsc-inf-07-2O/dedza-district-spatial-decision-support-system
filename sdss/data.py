"""SQLite database access, cached map layers and site-check data."""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache

import duckdb

from scripts.site_constraints import point_in_geometry
from scripts.site_suitability import SiteChecker

from . import config


def db() -> sqlite3.Connection:
    """Connection to the main analysis database, with rows as dicts."""
    if not config.DB_PATH.exists():
        raise FileNotFoundError(
            f"Database missing at {config.DB_PATH}. Run: python scripts/build_db.py"
        )
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Map layers loaded once from the database
# ---------------------------------------------------------------------------

def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _cell_feature(row: sqlite3.Row) -> dict:
    dist_km = round(row["dist_facility_m"] / 1000, 2) if row["dist_facility_m"] is not None else None
    return {
        "type": "Feature",
        "properties": {
            "cell_id": row["cell_id"],
            "area_km2": round(row["area_km2"] or 0, 3),
            "coverage": row["coverage"],
            "dist_km": dist_km,
            "road_km": round(row["road_km"] or 0, 2),
            "water_km": round(row["water_km"] or 0, 2),
            "facilities_in_cell": row["facilities_in_cell"],
            "population_2020": round(row["population_2020"] or 0),
            "population": round(row["population"] or 0),
            "population_2030": round(row["population_2030"] or 0),
            "growth": round(row["growth"] or 0),
            "road_access_share": round(row["road_access_share"] or 0, 3),
            "flood_area_share": round(row["flood_area_share"] or 0, 3),
            "flood_pop_share": round(row["flood_pop_share"] or 0, 3),
            "travel_min": None if row["travel_min"] is None else round(row["travel_min"], 1),
            "road_access": row["road_access"],
            "water_access": row["water_access"],
            "priority_score": round(row["priority_score"] or 0, 3),
        },
        "geometry": json.loads(row["geojson"]),
    }


@lru_cache(maxsize=1)
def load_payloads() -> dict:
    """Every layer and summary the API serves, read once and kept in memory."""
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

    cells = _feature_collection([_cell_feature(r) for r in con.execute("SELECT * FROM cells")])
    roads = _feature_collection([
        {
            "type": "Feature",
            "properties": {"highway": r["highway"] or "", "name": r["name"] or ""},
            "geometry": json.loads(r["geojson"]),
        }
        for r in con.execute("SELECT highway, name, geojson FROM roads")
    ])
    waterways = _feature_collection([
        {
            "type": "Feature",
            "properties": {"waterway": r["waterway"] or "", "name": r["name"] or ""},
            "geometry": json.loads(r["geojson"]),
        }
        for r in con.execute("SELECT waterway, name, geojson FROM waterways")
    ])
    type_counts = {
        r["facility_type"]: r["n"]
        for r in con.execute(
            "SELECT facility_type, COUNT(*) AS n FROM facilities GROUP BY facility_type"
        )
    }
    coverage = {
        r["coverage"]: {"cells": r["n"], "km2": round(r["km2"], 1)}
        for r in con.execute(
            "SELECT coverage, COUNT(*) AS n, SUM(area_km2) AS km2 FROM cells GROUP BY coverage"
        )
    }
    con.close()
    return {
        "meta": meta,
        "type_counts": type_counts,
        "coverage": coverage,
        "boundary": _feature_collection([
            {"type": "Feature", "properties": {"name": "Dedza District"}, "geometry": boundary}
        ]),
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
def load_traditional_authorities() -> dict:
    """Traditional Authority polygons clipped to Dedza (DuckDB Spatial)."""
    if not config.ADM2_PATH.exists() or not config.ADM3_PATH.exists():
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
        [str(config.ADM3_PATH), str(config.ADM2_PATH)],
    ).fetchall()
    con.close()

    return _feature_collection([
        {"type": "Feature", "properties": {"name": name}, "geometry": json.loads(geojson)}
        for name, geojson in rows
    ])


# ---------------------------------------------------------------------------
# Site constraints: building footprints, environmental layers, site checks
# ---------------------------------------------------------------------------

def site_layer_status() -> dict:
    """Which optional site-constraint layers are installed, with notes for the UI."""
    rasters = config.ENVIRONMENTAL_RASTER_PATHS
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
            "available": rasters["slope"].is_file(),
            "message": (
                "Slope is derived from Mapzen Terrain Tiles, a generalized terrain product "
                "based primarily on SRTM; use for preliminary screening only."
                if rasters["slope"].is_file()
                else "Terrain and slope are not installed. Run python scripts/import_environmental_layers.py."
            ),
        },
        "land_cover": {
            "available": rasters["land_cover"].is_file(),
            "message": (
                "ESA WorldCover 2021 v200 at 10 m resolution; land-cover classes are indicative, "
                "not parcel or wetland verification."
                if rasters["land_cover"].is_file()
                else "Land cover is not installed. Run python scripts/import_environmental_layers.py."
            ),
        },
        "flood": {
            "available": rasters["flood"].is_file(),
            "message": (
                "JRC Copernicus EMS GloFAS river flood hazard map v2.1: modelled water depth for a "
                "1-in-100-year flood at about 90 m. A global model, not an official national flood map."
                if rasters["flood"].is_file()
                else "Flood hazard is not installed. Run python scripts/import_planning_layers.py."
            ),
        },
    }
    if config.SITE_CONSTRAINT_DB_PATH.is_file():
        with sqlite3.connect(config.SITE_CONSTRAINT_DB_PATH) as con:
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
    """The detected building footprint containing the point, if any."""
    if not config.SITE_CONSTRAINT_DB_PATH.is_file():
        return None
    with sqlite3.connect(config.SITE_CONSTRAINT_DB_PATH) as con:
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


def buildings_in_bbox(west: float, south: float, east: float, north: float, limit: int) -> list[tuple]:
    """Up to `limit + 1` building rows (id, height, confidence, geometry) in a bounding box."""
    with sqlite3.connect(config.SITE_CONSTRAINT_DB_PATH) as con:
        return con.execute(
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


@lru_cache(maxsize=1)
def site_checker() -> SiteChecker:
    """Flood, road, slope and land-cover checks built from the stored layers."""
    con = db()
    try:
        placeholders = ",".join("?" * len(config.ACCESS_ROAD_CLASSES))
        roads = [
            row[0]
            for row in con.execute(
                f"SELECT geojson FROM roads WHERE lower(highway) IN ({placeholders})",
                config.ACCESS_ROAD_CLASSES,
            )
        ]
    finally:
        con.close()
    return SiteChecker(
        roads,
        float(load_payloads()["meta"].get("road_access_km", 2)),
        config.FLOOD_DEPTH_PATH,
        config.PERMANENT_WATER_PATH,
        config.ENVIRONMENTAL_RASTER_PATHS["slope"],
        config.ENVIRONMENTAL_RASTER_PATHS["land_cover"],
    )


def inside_district(point: tuple[float, float]) -> bool:
    return any(
        point_in_geometry(point, feature["geometry"])
        for feature in load_payloads()["boundary"]["features"]
    )
