"""Summarize WorldPop 2025 population counts by Dedza health-access zone and TA."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import duckdb
import numpy as np
import rasterio
from rasterio.features import geometry_mask, rasterize
from rasterio.mask import mask

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
ADM2_PATH = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"
ADM3_PATH = ROOT / "data" / "raw" / "MWI_ADM3_humanitarian.geojson"  # same file as sdss/config.py
DEFAULT_RASTER = ROOT / "data" / "raw" / "mwi_pop_2025_CN_100m_R2025A_v1.tif"
SOURCE_NAME = "WorldPop 2025 R2025A v1"


def load_boundaries() -> tuple[dict, list[tuple[str, dict]]]:
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        district = con.execute(
            """
            SELECT ST_AsGeoJSON(geom)
            FROM ST_Read(?)
            WHERE lower(shapeName) = 'dedza'
            """,
            [str(ADM2_PATH)],
        ).fetchone()
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
    finally:
        con.close()
    if not district:
        raise ValueError("Dedza district boundary was not found.")
    return json.loads(district[0]), [(name, json.loads(geojson)) for name, geojson in rows]


def load_cells() -> list[tuple[dict, int]]:
    codes = {"served": 1, "underserved": 2, "unserved": 3}
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("SELECT coverage, geojson FROM cells").fetchall()
    return [(json.loads(geometry), codes[coverage]) for coverage, geometry in rows]


def summarize(raster_path: Path) -> list[tuple[str, str, float]]:
    district, authorities = load_boundaries()
    cell_shapes = load_cells()
    with rasterio.open(raster_path) as source:
        clipped, transform = mask(
            source,
            [district],
            crop=True,
            filled=True,
            nodata=source.nodata,
        )
        population = clipped[0]
        shape = population.shape
        inside_district = geometry_mask(
            [district], out_shape=shape, transform=transform, invert=True
        )
        valid = inside_district & np.isfinite(population) & (population != source.nodata) & (population >= 0)
        cell_codes = rasterize(
            cell_shapes,
            out_shape=shape,
            transform=transform,
            fill=0,
            dtype="uint8",
            all_touched=False,
        )
        ta_names = sorted({name for name, _ in authorities})
        ta_code = {name: index for index, name in enumerate(ta_names, start=1)}
        ta_codes = rasterize(
            [(geometry, ta_code[name]) for name, geometry in authorities],
            out_shape=shape,
            transform=transform,
            fill=0,
            dtype="uint8",
            all_touched=False,
        )

    categories = [("served", 1), ("underserved", 2), ("unserved", 3), ("unclassified", 0)]
    summaries = []
    area_masks = [("Dedza District", inside_district)] + [
        (name, ta_codes == code) for name, code in ta_code.items()
    ]
    for area_name, area_mask in area_masks:
        area_valid = valid & area_mask
        total = float(population[area_valid].sum(dtype=np.float64))
        for category, code in categories:
            count = float(population[area_valid & (cell_codes == code)].sum(dtype=np.float64))
            summaries.append((area_name, category, count))
        summaries.append((area_name, "total", total))
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raster", nargs="?", type=Path, default=DEFAULT_RASTER)
    raster_path = parser.parse_args().raster.expanduser().resolve()
    if not DB_PATH.is_file():
        parser.error("Dedza database is missing; build it before importing population summaries.")
    if not raster_path.is_file():
        parser.error(f"WorldPop GeoTIFF not found: {raster_path}")

    rows = summarize(raster_path)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS population_summary (
                area_name TEXT,
                coverage TEXT,
                population_est REAL,
                source TEXT,
                year INTEGER,
                PRIMARY KEY (area_name, coverage, source, year)
            )
            """
        )
        con.execute("DELETE FROM population_summary WHERE source = ?", (SOURCE_NAME,))
        con.executemany(
            """
            INSERT INTO population_summary (area_name, coverage, population_est, source, year)
            VALUES (?, ?, ?, ?, 2025)
            """,
            [(area, coverage, value, SOURCE_NAME) for area, coverage, value in rows],
        )
    district_total = next(value for area, category, value in rows if area == "Dedza District" and category == "total")
    district_unserved = next(value for area, category, value in rows if area == "Dedza District" and category == "unserved")
    print(
        f"Imported population summaries for {len(rows) // 5} areas. "
        f"Dedza estimate: {district_total:,.0f}; in unserved health cells: {district_unserved:,.0f}."
    )


if __name__ == "__main__":
    main()