"""Import OSM populated places in Dedza into the `osm_places` naming table.

OpenStreetMap maps only ~71 named places in Dedza, too few for settlement-level
analysis, so the analysis settlements come from GRID3 (scripts/import_grid3_settlements.py).
These OSM names are used only to label the GRID3 settlements. Run this before
the GRID3 import if you rebuild from scratch.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
ADM2_PATH = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"


def load_places(archive: Path) -> list[tuple]:
    places_path = f"/vsizip/{archive.as_posix()}/populated_places_points.shp"
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        return con.execute(
            """
            SELECT COALESCE(p.name, p.name_en) AS name,
                   COALESCE(p.place, '') AS place,
                   ST_X(p.geom) AS lon,
                   ST_Y(p.geom) AS lat
            FROM ST_Read(?) AS p
            CROSS JOIN ST_Read(?) AS d
            WHERE lower(d.shapeName) = 'dedza'
              AND ST_Intersects(p.geom, d.geom)
              AND COALESCE(p.name, p.name_en) IS NOT NULL
            ORDER BY name
            """,
            [places_path, str(ADM2_PATH)],
        ).fetchall()
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("places_zip", type=Path, help="HOT OSM populated-places ZIP (hotosm_mwi_populated_places_osm_shp.zip)")
    archive = parser.parse_args().places_zip.expanduser().resolve()
    if not archive.is_file():
        parser.error(f"Populated-places ZIP not found: {archive}")
    if not DB_PATH.is_file():
        parser.error("Dedza database is missing; build it before importing places.")

    rows = load_places(archive)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS osm_places (id INTEGER PRIMARY KEY, name TEXT, place TEXT, lon REAL, lat REAL)"
        )
        con.execute("DELETE FROM osm_places")
        con.executemany("INSERT INTO osm_places (name, place, lon, lat) VALUES (?, ?, ?, ?)", rows)
    print(f"Imported {len(rows)} named OSM places in Dedza into osm_places ({DB_PATH})")


if __name__ == "__main__":
    main()
