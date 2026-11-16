"""Import OSM populated places spatially clipped to Dedza."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
ADM2_PATH = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"


def load_settlements(archive: Path) -> list[tuple]:
    places_path = f"/vsizip/{archive.as_posix()}/populated_places_points.shp"
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        return con.execute(
            """
            SELECT COALESCE(p.name, p.name_en, 'Unnamed settlement') AS name,
                   COALESCE(p.place, '') AS place,
                   TRY_CAST(p.population AS DOUBLE) AS population,
                   COALESCE(p.adm3_name, '') AS adm3_name,
                   ST_X(p.geom) AS lon,
                   ST_Y(p.geom) AS lat
            FROM ST_Read(?) AS p
            CROSS JOIN ST_Read(?) AS d
            WHERE lower(d.shapeName) = 'dedza'
              AND ST_Intersects(p.geom, d.geom)
            ORDER BY name
            """,
            [places_path, str(ADM2_PATH)],
        ).fetchall()
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("places_zip", type=Path, help="Path to populated-places ZIP")
    archive = parser.parse_args().places_zip.expanduser().resolve()
    if not archive.is_file():
        parser.error(f"Populated-places ZIP not found: {archive}")
    if not DB_PATH.is_file():
        parser.error("Dedza database is missing; build it before importing settlements.")
    if not ADM2_PATH.is_file():
        parser.error(f"District boundary is missing: {ADM2_PATH}")

    rows = load_settlements(archive)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY,
                name TEXT,
                place TEXT,
                population REAL,
                adm3_name TEXT,
                lon REAL NOT NULL,
                lat REAL NOT NULL
            )
            """
        )
        con.execute("DELETE FROM settlements")
        con.executemany(
            """
            INSERT INTO settlements (name, place, population, adm3_name, lon, lat)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    populated = sum(row[2] is not None for row in rows)
    print(
        f"Imported {len(rows)} Dedza settlements ({populated} with a population value) "
        f"into {DB_PATH}"
    )


if __name__ == "__main__":
    main()