"""Import the Malawi schools ZIP, spatially clipped to Dedza, into SQLite."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
ADM2_PATH = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"


def load_schools(archive: Path) -> list[tuple]:
    schools_path = f"/vsizip/{archive.as_posix()}/Dataset/mw_schools.shp"
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        return con.execute(
            """
            SELECT s.school_nam,
                   CAST(s.school_id AS VARCHAR),
                   s.status,
                   s.district,
                   ST_X(ST_Transform(s.geom, 'EPSG:4236', 'EPSG:4326')) AS lon,
                   ST_Y(ST_Transform(s.geom, 'EPSG:4236', 'EPSG:4326')) AS lat
            FROM ST_Read(?) AS s
            CROSS JOIN ST_Read(?) AS d
            WHERE lower(d.shapeName) = 'dedza'
              AND ST_Intersects(
                    ST_Transform(s.geom, 'EPSG:4236', 'EPSG:4326'), d.geom
              )
            ORDER BY s.school_nam
            """,
            [schools_path, str(ADM2_PATH)],
        ).fetchall()
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_zip", type=Path, help="Path to Dataset.zip")
    archive = parser.parse_args().dataset_zip.expanduser().resolve()
    if not archive.is_file():
        parser.error(f"Dataset ZIP not found: {archive}")
    if not DB_PATH.is_file():
        parser.error("Dedza database is missing; build it before importing schools.")
    if not ADM2_PATH.is_file():
        parser.error(f"District boundary is missing: {ADM2_PATH}")

    rows = load_schools(archive)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS schools (
                id INTEGER PRIMARY KEY,
                name TEXT,
                school_id TEXT,
                status TEXT,
                district TEXT,
                lon REAL NOT NULL,
                lat REAL NOT NULL
            )
            """
        )
        con.execute("DELETE FROM schools")
        con.executemany(
            """
            INSERT INTO schools (name, school_id, status, district, lon, lat)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    print(f"Imported {len(rows)} schools spatially clipped to Dedza into {DB_PATH}")


if __name__ == "__main__":
    main()