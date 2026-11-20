"""Import Dedza settlements from GRID3 Malawi Settlement Extents v3.0 into SQLite.

Source: GRID3 MWI - Settlement Extents v3.0 (CIESIN, Columbia University; GRID3),
CC BY-SA, https://data.humdata.org/dataset/grid3-mwi-settlement-extents-v3-0
Download GRID3_MWI_settlement_extents_v3_0.zip, extract it, and pass the .gpkg path.

OpenStreetMap maps only 71 named places in Dedza, far too few for settlement-level
access analysis. GRID3 delineates every settled area from building footprints.
This script keeps "Built-up Area" and "Small Settlement Area" polygons (50 or more
buildings) and, for each one:
  * uses a point on the polygon surface as its location,
  * assigns the Traditional Authority by point-in-polygon (gbHumanitarian ADM3),
  * estimates population as the WorldPop 2025 sum inside the polygon,
  * borrows a name from the nearest OSM place (GRID3 has no names).
The OSM places are kept in `osm_places` and used only for naming.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

import duckdb
import rasterio
from rasterio.mask import mask
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
BOUNDARY_PATH = ROOT / "data" / "raw" / "dedza_boundary.geojson"
ADM2_PATH = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"
ADM3_PATH = ROOT / "data" / "raw" / "MWI_ADM3_humanitarian.geojson"
WORLDPOP_PATH = ROOT / "data" / "raw" / "mwi_pop_2025_CN_100m_R2025A_v1.tif"
KEEP_TYPES = ("Built-up Area", "Small Settlement Area")
SAME_PLACE_KM = 1.5   # an OSM place this close names the settlement directly
NEAR_PLACE_KM = 8.0   # beyond this the settlement is named by TA only


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (a[0] - b[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110.57
    return math.hypot(dx, dy)


def load_grid3(gpkg: Path) -> list[tuple]:
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
        con.execute("SET geometry_always_xy = true;")
        return con.execute(
            f"""
            SELECT s.OBJECTID, s.type, s.building_count, s.building_area, s.probability,
                   ST_X(ST_PointOnSurface(s.Shape)), ST_Y(ST_PointOnSurface(s.Shape)),
                   ST_AsGeoJSON(s.Shape)
            FROM ST_Read(?) AS s, ST_Read(?) AS d
            WHERE lower(d.shapeName) = 'dedza'
              AND s.type IN {KEEP_TYPES}
              AND ST_Intersects(ST_PointOnSurface(s.Shape), d.geom)
            ORDER BY s.building_count DESC
            """,
            [str(gpkg), str(ADM2_PATH)],
        ).fetchall()
    finally:
        con.close()


def osm_places(con: sqlite3.Connection) -> list[tuple[str, float, float]]:
    """OSM named places, copied once from the original OSM settlements table."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS osm_places (id INTEGER PRIMARY KEY, name TEXT, place TEXT, lon REAL, lat REAL)"
    )
    if not con.execute("SELECT COUNT(*) FROM osm_places").fetchone()[0]:
        columns = {row[1] for row in con.execute("PRAGMA table_info(settlements)")}
        if "source" not in columns:  # still the OSM import
            con.execute(
                "INSERT INTO osm_places (name, place, lon, lat) SELECT name, place, lon, lat FROM settlements"
            )
    return [(n, lon, lat) for n, lon, lat in con.execute("SELECT name, lon, lat FROM osm_places") if n]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gpkg", type=Path, help="GRID3_MWI_settlement_extents_v3_0.gpkg")
    gpkg = parser.parse_args().gpkg.expanduser().resolve()
    if not gpkg.is_file():
        parser.error(f"GRID3 GeoPackage not found: {gpkg}")

    rows = load_grid3(gpkg)
    tas = [
        (f["properties"]["shapeName"], shape(f["geometry"]))
        for f in json.loads(ADM3_PATH.read_text(encoding="utf-8"))["features"]
    ]

    with sqlite3.connect(DB_PATH) as con:
        places = osm_places(con)
        records = []
        used_names: dict[str, int] = {}
        with rasterio.open(WORLDPOP_PATH) as raster:
            for oid, kind, buildings, area, probability, lon, lat, geojson in rows:
                point = Point(lon, lat)
                ta = next((name for name, poly in tas if poly.contains(point)), "")
                try:
                    values, _ = mask(raster, [json.loads(geojson)], crop=True, filled=False)
                    population = float(values.sum()) if values.count() else 0.0
                except ValueError:  # polygon smaller than a pixel
                    population = 0.0
                nearest = min(places, key=lambda p: km((lon, lat), (p[1], p[2])), default=None)
                distance = km((lon, lat), (nearest[1], nearest[2])) if nearest else math.inf
                if distance <= SAME_PLACE_KM:
                    base = nearest[0]
                elif distance <= NEAR_PLACE_KM:
                    base = f"Near {nearest[0]}"
                else:
                    base = f"{ta or 'Dedza'} settlement"
                used_names[base] = used_names.get(base, 0) + 1
                name = base if used_names[base] == 1 else f"{base} ({used_names[base]})"
                records.append((
                    name,
                    "Built-up area" if kind == "Built-up Area" else "Small settlement area",
                    round(population, 1),
                    ta,
                    lon,
                    lat,
                    int(buildings),
                    "GRID3 v3.0",
                    int(oid),
                ))

        con.execute("DROP TABLE IF EXISTS settlements")
        con.execute(
            """
            CREATE TABLE settlements (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                place TEXT,
                population REAL,
                adm3_name TEXT,
                lon REAL NOT NULL,
                lat REAL NOT NULL,
                building_count INTEGER,
                source TEXT,
                source_id INTEGER
            )
            """
        )
        con.executemany(
            """INSERT INTO settlements
               (name, place, population, adm3_name, lon, lat, building_count, source, source_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            records,
        )
    total = sum(r[2] for r in records)
    print(f"Imported {len(records)} GRID3 settlements ({total:,.0f} WorldPop residents inside them) into {DB_PATH}")


if __name__ == "__main__":
    main()
