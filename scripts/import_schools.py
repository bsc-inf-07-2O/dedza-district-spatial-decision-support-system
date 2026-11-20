"""Import the Malawi schools layer, spatially clipped to Dedza, into SQLite.

The source shapefile's .prj declares the Hu Tzu Shan datum (EPSG:4236, Taiwan),
but its coordinates are plain WGS 84 longitude/latitude. Reprojecting from
EPSG:4236 shifted every school by about 370 m. Checked against Microsoft building
footprints (2026-11): the raw points sit a median 48 m from the nearest building
(106/289 within 30 m) versus 74 m (77/289) after the bogus shift. The geometry is
therefore relabelled as EPSG:4326 with no datum shift. (The layer's xcoord/ycoord
UTM columns are ~140 m off in either case and are not used.)
Misspelled status values ("Actve", "Non _ Actine", ...) are normalised.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
ADM2_PATH = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"


def normalise_status(value: object) -> str | None:
    """Map the source's free-text status to 'Active' / 'Not active'."""
    text = re.sub(r"[^a-z]", "", str(value or "").lower())
    if not text:
        return None
    if text.startswith("non") or text.startswith("not") or "inact" in text:
        return "Not active"
    if text.startswith("ac"):
        return "Active"
    return None


def source_path(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        return f"/vsizip/{path.as_posix()}/Dataset/mw_schools.shp"
    return str(path)


def load_schools(path: Path) -> list[tuple]:
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        rows = con.execute(
            """
            WITH s AS (
                -- Relabel (not reproject) the mis-declared CRS as WGS 84.
                SELECT *, ST_SetCRS(geom, 'EPSG:4326') AS g FROM ST_Read(?)
            )
            SELECT s.school_nam,
                   CAST(s.school_id AS VARCHAR),
                   s.status,
                   s.district,
                   ST_X(s.g) AS lon,
                   ST_Y(s.g) AS lat
            FROM s
            CROSS JOIN ST_Read(?) AS d
            WHERE lower(d.shapeName) = 'dedza'
              AND ST_Intersects(s.g, d.geom)
            ORDER BY s.school_nam
            """,
            [source_path(path), str(ADM2_PATH)],
        ).fetchall()
    finally:
        con.close()

    # Sanity check: every point must be a plausible Malawi longitude/latitude.
    bad = [row for row in rows if not (32.5 < row[4] < 36.0 and -17.5 < row[5] < -9.0)]
    if bad:
        raise RuntimeError(f"{len(bad)} school points are outside Malawi's lon/lat range; check the source CRS.")
    return [
        (name, school_id, normalise_status(status), district, lon, lat)
        for name, school_id, status, district, lon, lat in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Dataset.zip or the mw_schools.shp file")
    source = parser.parse_args().source.expanduser().resolve()
    if not source.is_file():
        parser.error(f"Schools source not found: {source}")
    if not DB_PATH.is_file():
        parser.error("Dedza database is missing; build it before importing schools.")
    if not ADM2_PATH.is_file():
        parser.error(f"District boundary is missing: {ADM2_PATH}")

    rows = load_schools(source)
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
    active = sum(1 for row in rows if row[2] == "Active")
    print(f"Imported {len(rows)} schools ({active} active) spatially clipped to Dedza into {DB_PATH}")


if __name__ == "__main__":
    main()
