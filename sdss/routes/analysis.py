"""District summaries, health-gap thresholds and priority scoring."""

from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

from .. import config
from ..data import db, load_payloads
from ..planning import priority_baseline

bp = Blueprint("analysis", __name__, url_prefix="/api")


@bp.get("/health")
def health():
    database_ready = config.DB_PATH.exists()
    return jsonify({
        "status": "ok" if database_ready else "missing_database",
        "database_ready": database_ready,
        "database_path": str(config.DB_PATH),
    })


@bp.get("/summary")
def summary():
    data = load_payloads()
    return jsonify({
        "meta": data["meta"],
        "type_counts": data["type_counts"],
        "coverage": data["coverage"],
        "recommendations": data["recommendations"],
        "n_registry_facilities": len(data["registry"]),
    })


@bp.get("/population-summary")
def population_summary():
    return jsonify(load_payloads()["population_summary"])


# Coverage class for a cell from its stored walking distance to care.
_COVERAGE_CASE = """
    CASE
        WHEN dist_facility_m IS NULL THEN 'unserved'
        WHEN dist_facility_m <= ? THEN 'served'
        WHEN dist_facility_m <= ? THEN 'underserved'
        ELSE 'unserved'
    END
"""


@bp.get("/gap-analysis")
def gap_analysis():
    """Reclassify every cell with user-chosen served / underserved thresholds."""
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
        return jsonify({"error": "Need 0 < served_km < underserved_km <= 50."}), 400

    thresholds = (served_km * 1000, underserved_km * 1000)
    con = db()
    try:
        coverage_rows = con.execute(
            f"""
            WITH classified AS (
                SELECT area_km2, {_COVERAGE_CASE} AS coverage
                FROM cells
            )
            SELECT coverage, COUNT(*) AS cells, ROUND(SUM(area_km2), 1) AS km2
            FROM classified
            GROUP BY coverage
            """,
            thresholds,
        ).fetchall()
        cell_rows = con.execute(
            f"SELECT cell_id, {_COVERAGE_CASE} AS coverage FROM cells ORDER BY cell_id",
            thresholds,
        ).fetchall()
    finally:
        con.close()

    coverage = {
        "served": {"cells": 0, "km2": 0},
        "underserved": {"cells": 0, "km2": 0},
        "unserved": {"cells": 0, "km2": 0},
    }
    for row in coverage_rows:
        coverage[row["coverage"]] = {"cells": row["cells"], "km2": row["km2"] or 0}
    return jsonify({
        "served_km": served_km,
        "underserved_km": underserved_km,
        "coverage": coverage,
        "cells": [dict(row) for row in cell_rows],
    })


@bp.get("/priority-scenario")
def priority_scenario():
    """Re-score every grid cell in SQL with user-chosen criterion weights.

    Uses the same normalisation as the build-time score in scripts/build_db.py,
    with the scales stored in the meta table: projected 2030 residents and
    2025-2030 growth reach a full score at their stored percentile values,
    walking distance to care at distance_full_m, road access and flood safety
    are already shares (0-1), and served cells are multiplied by
    served_multiplier. Weights are normalised to sum to 1, so the stored
    baseline weights reproduce the baseline ranking.
    """
    baseline_weights, scales = priority_baseline()
    try:
        raw = {
            key: float(request.args.get(f"w_{key}", baseline_weights[key]))
            for key in config.PRIORITY_CRITERIA
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

    return jsonify({
        "weights": {key: round(value * 100, 1) for key, value in weights.items()},
        "baseline_weights": baseline_weights,
        "scales": scales,
        "served_km": served_km,
        "min_ranked_cell_km2": config.MIN_RANKED_CELL_KM2,
        "top_cell_ids": [
            row["cell_id"] for row in rows if (row["area_km2"] or 0) >= config.MIN_RANKED_CELL_KM2
        ][:12],
        "cells": [{"cell_id": row["cell_id"], "score": row["score"]} for row in rows],
    })
