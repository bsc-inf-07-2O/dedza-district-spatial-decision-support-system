"""Clip Lab 4 Malawi GIS layers to Dedza District and build the analysis database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = PROCESSED / "dedza.sqlite"

ADM2 = RAW / "MWI_ADM2_simplified.geojson"

MAJOR_ROADS = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "primary_link",
    "secondary_link",
    "tertiary_link",
)


def classify_facility(name: str) -> str:
    n = (name or "").lower()
    if "hospital" in n:
        return "Hospital"
    if "health centre" in n or "health center" in n:
        return "Health Centre"
    if "dispensary" in n:
        return "Dispensary"
    if "clinic" in n:
        return "Clinic"
    if "post" in n:
        return "Health Post"
    return "Other health facility"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial;")
    con.execute("LOAD spatial;")
    con.execute("SET geometry_always_xy = true;")
    return con


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=ROOT / "data" / "source",
        help="Lab 4 data directory (defaults to data/source)",
    )
    source_dir = parser.parse_args().source_dir.expanduser().resolve()
    facilities_path = source_dir / "Facilities" / "Facilities" / "facilities.shp"
    roads_path = source_dir / "hotosm_mwi_roads_osm_shp" / "roads_lines.shp"
    waterways_path = source_dir / "hotosm_mwi_waterways_osm_shp" / "waterways_lines.shp"
    missing = [
        path for path in (facilities_path, roads_path, waterways_path) if not path.is_file()
    ]
    if missing:
        parser.error(
            "Required source shapefile(s) not found: "
            + ", ".join(str(path) for path in missing)
        )

    PROCESSED.mkdir(parents=True, exist_ok=True)
    print("Loading Dedza boundary...")
    con = connect()

    con.execute(
        """
        CREATE OR REPLACE TABLE dedza AS
        SELECT shapeName AS name, geom
        FROM ST_Read(?)
        WHERE lower(shapeName) = 'dedza'
        """,
        [str(ADM2)],
    )
    xmin, ymin, xmax, ymax, km2 = con.execute(
        """
        SELECT ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom),
               ST_Area(ST_Transform(geom, 'EPSG:4326', 'EPSG:32736')) / 1e6
        FROM dedza
        """
    ).fetchone()
    print(f"Dedza bbox: {xmin:.4f},{ymin:.4f} -> {xmax:.4f},{ymax:.4f}  area={km2:.1f} km2")

    boundary_geojson = con.execute("SELECT ST_AsGeoJSON(geom) FROM dedza").fetchone()[0]
    (RAW / "dedza_boundary.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"name": "Dedza"}, "geometry": json.loads(boundary_geojson)}
        ]}),
        encoding="utf-8",
    )

    print("Clipping health facilities...")
    con.execute(
        """
        CREATE OR REPLACE TABLE facilities_all AS
        SELECT
            COALESCE(Field4, '') AS name,
            COALESCE(Field1, '') AS owner,
            COALESCE(Field2, '') AS zone,
            COALESCE(Field3, '') AS dho,
            COALESCE(Field8, '') AS uid,
            TRY_CAST(Field6 AS DOUBLE) AS lon,
            TRY_CAST(Field7 AS DOUBLE) AS lat,
            geom
        FROM ST_Read(?)
        """,
        [str(facilities_path)],
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE facilities AS
        SELECT
            f.name, f.owner, f.zone, f.dho, f.uid, f.lon, f.lat,
            ST_Point(f.lon, f.lat) AS geom
        FROM facilities_all f, dedza d
        WHERE f.lon IS NOT NULL AND f.lat IS NOT NULL
          AND ST_Intersects(ST_Point(f.lon, f.lat), d.geom)
        """
    )
    n_fac = con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]
    print(f"Facilities in Dedza: {n_fac}")

    clip_db = PROCESSED / "clip.duckdb"
    envelope = f"ST_MakeEnvelope({xmin - 0.02}, {ymin - 0.02}, {xmax + 0.02}, {ymax + 0.02})"
    if clip_db.exists():
        print("Loading previously clipped roads and waterways...")
        con.execute(f"ATTACH '{clip_db.as_posix()}' AS clip (READ_ONLY);")
        con.execute("CREATE OR REPLACE TABLE roads_clip AS SELECT * FROM clip.roads_clip;")
        con.execute("CREATE OR REPLACE TABLE water_clip AS SELECT * FROM clip.water_clip;")
        con.execute("DETACH clip;")
    else:
        print("Clipping roads (this can take a few minutes)...")
        con.execute(
            f"""
            CREATE OR REPLACE TABLE roads_clip AS
            SELECT r.*
            FROM ST_ReadSHP('{roads_path.as_posix()}') r, dedza d
            WHERE ST_Intersects(r.geom, {envelope})
              AND ST_Intersects(r.geom, d.geom)
            """
        )
        print("Clipping waterways...")
        con.execute(
            f"""
            CREATE OR REPLACE TABLE water_clip AS
            SELECT w.*
            FROM ST_ReadSHP('{waterways_path.as_posix()}') w, dedza d
            WHERE ST_Intersects(w.geom, {envelope})
              AND ST_Intersects(w.geom, d.geom)
            """
        )
        print("Caching clipped layers...")
        con.execute(f"ATTACH '{clip_db.as_posix()}' AS clip;")
        con.execute("CREATE TABLE clip.roads_clip AS SELECT * FROM roads_clip;")
        con.execute("CREATE TABLE clip.water_clip AS SELECT * FROM water_clip;")
        con.execute("DETACH clip;")

    n_roads = con.execute("SELECT COUNT(*) FROM roads_clip").fetchone()[0]
    print(f"Road segments intersecting Dedza: {n_roads}")
    print("Road columns:", [r[0] for r in con.execute("DESCRIBE roads_clip").fetchall()])
    n_water = con.execute("SELECT COUNT(*) FROM water_clip").fetchone()[0]
    print(f"Waterway segments intersecting Dedza: {n_water}")
    print("Water columns:", [r[0] for r in con.execute("DESCRIBE water_clip").fetchall()])

    # Detect highway / waterway attribute names (shp truncates to 10 chars).
    road_cols = [r[0] for r in con.execute("DESCRIBE roads_clip").fetchall()]
    water_cols = [r[0] for r in con.execute("DESCRIBE water_clip").fetchall()]
    highway_col = next((c for c in road_cols if c.lower() in ("highway", "fclass", "type")), None)
    road_name_col = next((c for c in road_cols if c.lower() in ("name", "nam", "road_name")), None)
    water_col = next((c for c in water_cols if c.lower() in ("waterway", "fclass", "type")), None)
    water_name_col = next((c for c in water_cols if c.lower() in ("name", "nam")), None)

    highway_sql = f"COALESCE(CAST(r.{highway_col} AS VARCHAR), '')" if highway_col else "''"
    road_name_sql = f"COALESCE(CAST(r.{road_name_col} AS VARCHAR), '')" if road_name_col else "''"
    water_sql = f"COALESCE(CAST(w.{water_col} AS VARCHAR), '')" if water_col else "''"
    water_name_sql = f"COALESCE(CAST(w.{water_name_col} AS VARCHAR), '')" if water_name_col else "''"

    print("Building 3 km analysis grid and gap scores...")
    con.execute(
        """
        CREATE OR REPLACE TABLE cells AS
        WITH b AS (
            SELECT ST_Transform(geom, 'EPSG:4326', 'EPSG:32736') AS g FROM dedza
        ),
        ext AS (
            SELECT ST_XMin(g) AS xmin, ST_YMin(g) AS ymin, ST_XMax(g) AS xmax, ST_YMax(g) AS ymax
            FROM b
        ),
        nx AS (
            SELECT CAST(ceil((xmax - xmin) / 3000.0) AS INTEGER) AS n FROM ext
        ),
        ny AS (
            SELECT CAST(ceil((ymax - ymin) / 3000.0) AS INTEGER) AS n FROM ext
        ),
        grid_utm AS (
            SELECT ST_MakeEnvelope(
                xmin + 3000 * x.i,
                ymin + 3000 * y.i,
                xmin + 3000 * (x.i + 1),
                ymin + 3000 * (y.i + 1)
            ) AS geom_utm
            FROM ext,
                 range((SELECT n FROM nx)) AS x(i),
                 range((SELECT n FROM ny)) AS y(i)
        ),
        grid AS (
            SELECT ST_Transform(geom_utm, 'EPSG:32736', 'EPSG:4326') AS geom
            FROM grid_utm
        ),
        clipped AS (
            SELECT ST_Intersection(c.geom, d.geom) AS geom
            FROM grid c, dedza d
            WHERE ST_Intersects(c.geom, d.geom)
              AND ST_Area(ST_Transform(
                    ST_Intersection(c.geom, d.geom), 'EPSG:4326', 'EPSG:32736'
              )) > 200000
        )
        SELECT
            ROW_NUMBER() OVER () AS cell_id,
            geom,
            ST_Centroid(geom) AS centroid,
            ST_Area(ST_Transform(geom, 'EPSG:4326', 'EPSG:32736')) / 1e6 AS area_km2
        FROM clipped
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE cell_stats AS
        WITH fac_dist AS (
            SELECT c.cell_id,
                   MIN(ST_Distance(
                        ST_Transform(c.centroid, 'EPSG:4326', 'EPSG:32736'),
                        ST_Transform(f.geom, 'EPSG:4326', 'EPSG:32736')
                   )) AS dist_facility_m,
                   COUNT(*) FILTER (WHERE ST_Intersects(f.geom, c.geom)) AS facilities_in_cell
            FROM cells c
            CROSS JOIN facilities f
            GROUP BY c.cell_id
        ),
        road_len AS (
            SELECT c.cell_id,
                   SUM(ST_Length(ST_Transform(
                        ST_Intersection(r.geom, c.geom), 'EPSG:4326', 'EPSG:32736'
                   ))) / 1000.0 AS road_km
            FROM cells c
            JOIN roads_clip r ON ST_Intersects(r.geom, c.geom)
            GROUP BY c.cell_id
        ),
        water_len AS (
            SELECT c.cell_id,
                   SUM(ST_Length(ST_Transform(
                        ST_Intersection(w.geom, c.geom), 'EPSG:4326', 'EPSG:32736'
                   ))) / 1000.0 AS water_km
            FROM cells c
            JOIN water_clip w ON ST_Intersects(w.geom, c.geom)
            GROUP BY c.cell_id
        )
        SELECT
            c.cell_id,
            c.area_km2,
            ST_X(c.centroid) AS lon,
            ST_Y(c.centroid) AS lat,
            f.dist_facility_m,
            COALESCE(r.road_km, 0) AS road_km,
            COALESCE(w.water_km, 0) AS water_km,
            COALESCE(f.facilities_in_cell, 0) AS facilities_in_cell,
            c.geom
        FROM cells c
        LEFT JOIN fac_dist f USING (cell_id)
        LEFT JOIN road_len r USING (cell_id)
        LEFT JOIN water_len w USING (cell_id)
        """
    )

    stats = con.execute(
        """
        SELECT
            COUNT(*) AS n_cells,
            SUM(CASE WHEN dist_facility_m <= 5000 THEN 1 ELSE 0 END) AS served,
            SUM(CASE WHEN dist_facility_m > 5000 AND dist_facility_m <= 8000 THEN 1 ELSE 0 END) AS underserved,
            SUM(CASE WHEN dist_facility_m > 8000 THEN 1 ELSE 0 END) AS unserved,
            AVG(dist_facility_m) AS avg_dist,
            SUM(area_km2) AS area
        FROM cell_stats
        """
    ).fetchone()
    print("Grid stats:", stats)

    print("Exporting layers to SQLite...")
    if DB_PATH.exists():
        DB_PATH.unlink()
    sql = sqlite3.connect(DB_PATH)
    cur = sql.cursor()
    cur.executescript(
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE facilities (
            id INTEGER PRIMARY KEY,
            name TEXT,
            facility_type TEXT,
            owner TEXT,
            dho TEXT,
            uid TEXT,
            lon REAL,
            lat REAL
        );
        CREATE TABLE roads (
            id INTEGER PRIMARY KEY,
            highway TEXT,
            name TEXT,
            geojson TEXT
        );
        CREATE TABLE waterways (
            id INTEGER PRIMARY KEY,
            waterway TEXT,
            name TEXT,
            geojson TEXT
        );
        CREATE TABLE cells (
            cell_id INTEGER PRIMARY KEY,
            lon REAL,
            lat REAL,
            area_km2 REAL,
            dist_facility_m REAL,
            road_km REAL,
            water_km REAL,
            facilities_in_cell INTEGER,
            coverage TEXT,
            road_access TEXT,
            water_access TEXT,
            priority_score REAL,
            geojson TEXT
        );
        CREATE TABLE recommendations (
            rank INTEGER PRIMARY KEY,
            cell_id INTEGER,
            lon REAL,
            lat REAL,
            coverage TEXT,
            dist_facility_km REAL,
            road_km REAL,
            water_km REAL,
            priority_score REAL,
            action TEXT
        );
        CREATE TABLE boundary (
            name TEXT,
            geojson TEXT
        );
        """
    )

    cur.execute(
        "INSERT INTO boundary VALUES (?, ?)",
        ("Dedza", json.dumps(json.loads(boundary_geojson))),
    )

    fac_rows = con.execute("SELECT name, owner, dho, uid, lon, lat FROM facilities").fetchall()
    for name, owner, dho, uid, lon, lat in fac_rows:
        cur.execute(
            "INSERT INTO facilities (name, facility_type, owner, dho, uid, lon, lat) VALUES (?,?,?,?,?,?,?)",
            (name, classify_facility(name), owner, dho, uid, lon, lat),
        )

    # Keep map-friendly road subset: major classes, simplified.
    road_rows = con.execute(
        f"""
        SELECT {highway_sql} AS highway, {road_name_sql} AS name,
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(r.geom, d.geom), 0.00025))
        FROM roads_clip r, dedza d
        WHERE lower({highway_sql}) IN {MAJOR_ROADS}
        """
    ).fetchall()
    kept = 0
    for highway, name, gj in road_rows:
        if not gj:
            continue
        cur.execute("INSERT INTO roads (highway, name, geojson) VALUES (?,?,?)", (highway, name, gj))
        kept += 1
    print(f"Roads stored for map: {kept}")

    water_rows = con.execute(
        f"""
        SELECT {water_sql}, {water_name_sql},
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(w.geom, d.geom), 0.0003))
        FROM water_clip w, dedza d
        WHERE ST_Length(ST_Transform(ST_Intersection(w.geom, d.geom), 'EPSG:4326', 'EPSG:32736')) > 250
        """
    ).fetchall()
    for waterway, name, gj in water_rows:
        if gj:
            cur.execute("INSERT INTO waterways (waterway, name, geojson) VALUES (?,?,?)", (waterway, name, gj))
    print("Waterways stored:", cur.execute("SELECT COUNT(*) FROM waterways").fetchone()[0])

    cell_rows = con.execute(
        """
        SELECT cell_id, lon, lat, area_km2, dist_facility_m, road_km, water_km,
               facilities_in_cell, ST_AsGeoJSON(geom)
        FROM cell_stats
        """
    ).fetchall()
    recs = []
    for cell_id, lon, lat, area_km2, dist_m, road_km, water_km, n_in, gj in cell_rows:
        dist_m = float(dist_m or 0)
        road_km = float(road_km or 0)
        water_km = float(water_km or 0)
        if dist_m <= 5000:
            coverage = "served"
        elif dist_m <= 8000:
            coverage = "underserved"
        else:
            coverage = "unserved"
        road_access = "good" if road_km >= 4 else ("limited" if road_km >= 1.5 else "poor")
        water_access = "good" if water_km >= 2 else ("limited" if water_km >= 0.5 else "poor")
        priority = (
            min(dist_m / 8000.0, 2.0) * 0.55
            + (1 - min(road_km / 8.0, 1)) * 0.25
            + (1 - min(water_km / 4.0, 1)) * 0.20
        )
        if coverage == "served":
            priority *= 0.35
        cur.execute(
            """INSERT INTO cells (
                cell_id, lon, lat, area_km2, dist_facility_m, road_km, water_km,
                facilities_in_cell, coverage, road_access, water_access,
                priority_score, geojson
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(cell_id), float(lon), float(lat), float(area_km2), dist_m,
                road_km, water_km, int(n_in), coverage, road_access, water_access,
                float(priority), gj,
            ),
        )
        recs.append((priority, cell_id, lon, lat, coverage, dist_m, road_km, water_km))

    recs.sort(reverse=True)
    for rank, (priority, cell_id, lon, lat, coverage, dist_m, road_km, water_km) in enumerate(recs[:12], start=1):
        actions = []
        if dist_m > 8000:
            actions.append("Site a new health post or outreach clinic")
        elif dist_m > 5000:
            actions.append("Upgrade outreach / mobile clinic coverage")
        if road_km < 1.5:
            actions.append("Prioritise feeder-road improvement")
        if water_km < 0.5:
            actions.append("Assess WASH / borehole need (far from mapped waterways)")
        if not actions:
            actions.append("Monitor service quality and staffing")
        cur.execute(
            """INSERT INTO recommendations (
                rank, cell_id, lon, lat, coverage, dist_facility_km, road_km, water_km,
                priority_score, action
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                rank, int(cell_id), float(lon), float(lat), coverage,
                round(dist_m / 1000, 2), round(road_km, 2), round(water_km, 2),
                round(priority, 3), "; ".join(actions),
            ),
        )

    n_cells, served, underserved, unserved, avg_dist, area = stats
    meta = {
        "district": "Dedza",
        "country": "Malawi",
        "n_facilities": str(n_fac),
        "n_road_segments_clip": str(n_roads),
        "n_water_segments_clip": str(n_water),
        "n_cells": str(int(n_cells)),
        "served_cells": str(int(served or 0)),
        "underserved_cells": str(int(underserved or 0)),
        "unserved_cells": str(int(unserved or 0)),
        "avg_dist_facility_m": str(round(float(avg_dist or 0), 1)),
        "district_area_km2": str(round(float(km2), 1)),
        "grid_km": "3",
        "health_access_standard_km": "5",
        "crs": "EPSG:4326",
        "analysis_crs": "EPSG:32736",
        "sources": "MOH Malawi health facilities shapefile; HOT OSM roads & waterways; geoBoundaries MWI ADM2",
    }
    for k, v in meta.items():
        cur.execute("INSERT INTO meta VALUES (?,?)", (k, v))

    sql.commit()
    sql.close()
    print("Wrote", DB_PATH)


if __name__ == "__main__":
    main()
