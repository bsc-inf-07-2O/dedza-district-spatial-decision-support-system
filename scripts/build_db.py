"""Clip Lab 4 Malawi GIS layers to Dedza District and build the analysis database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import duckdb

if __package__ in (None, ""):  # run as `python scripts/build_db.py`
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.planning_analysis import (  # noqa: E402
    PLANNING_LAYER_PATHS,
    ROAD_ACCESS_KM,
    cell_indicators,
    planning_grids,
    network_access,
)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = PROCESSED / "dedza.sqlite"

ADM2 = RAW / "MWI_ADM2_simplified.geojson"

# Baseline weights of the facility-siting priority score, stored in the meta
# table (the app reads them from there). Every criterion is scaled 0-1:
#   population - residents projected for 2030 (demand the facility must serve)
#   growth     - residents added 2025-2030 (supports the growing population)
#   distance   - walking distance along roads and paths to the nearest facility (need)
#   road       - share of residents within ROAD_ACCESS_KM of a classified road
#   flood      - share of the cell outside the 1-in-100-year flood zone
PRIORITY_WEIGHTS = {"population": 0.30, "growth": 0.15, "distance": 0.30, "road": 0.15, "flood": 0.10}
# Population and growth reach a full score at this percentile of the cells, so
# the scale comes from the data rather than a fixed number. The 90th percentile
# keeps the most populated cells distinguishable from each other.
FULL_SCORE_PERCENTILE = 0.9
# Walking distance reaches a full score at 12 km (about 2.4 hours on foot), so
# cells beyond the 8 km "unserved" threshold are still ranked by how far they are.
DISTANCE_FULL_M = 12000.0
SERVED_MULTIPLIER = 0.35

# Facilities in neighbouring districts within this distance of the boundary are
# included in the nearest-facility distance (not in the Dedza facility layer).
# 20 km exceeds the 16 km distance cap used by the priority score.
CROSS_BORDER_KM = 20.0

# Border slivers smaller than this are scored but not ranked as recommendations.
MIN_RANKED_CELL_KM2 = 1.0
SOURCES = (
    "Ministry of Health Malawi health facilities (baseline); "
    "Malawi Health Facility Registry (MHFR, Ministry of Health); "
    "Malawi schools point dataset; "
    "GRID3 Malawi Settlement Extents v3.0 (CIESIN, CC BY-SA 4.0); "
    "WorldPop 2020, 2025 and 2030 (projected) constrained population R2025A v1 (CC BY 4.0); "
    "JRC Copernicus EMS GloFAS global river flood hazard map v2.1, 1-in-100-year (ODbL 1.0); "
    "NSO Malawi Traditional Authority boundaries via geoBoundaries/HDX (CC BY 3.0 IGO); "
    "geoBoundaries MWI ADM2 district boundary; "
    "HOT OpenStreetMap roads, waterways and place names (ODbL); "
    "Microsoft Global ML Building Footprints (CDLA Permissive 2.0); "
    "ESA WorldCover 2021 v200 (CC BY 4.0); Mapzen Terrain Tiles (slope)"
)

# Tables created by other import scripts; a rebuild carries them over.
IMPORTED_TABLES = ("schools", "settlements", "osm_places", "mhfr_facilities", "population_summary")

# Classified roads (OSM trunk to tertiary): the district and national network,
# used for the road-access indicator. Many OSM "residential"/"unclassified"
# ways in rural Malawi are village tracks, so they are not counted here.
ACCESS_ROADS = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "primary_link",
    "secondary_link",
    "tertiary_link",
)

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
    parser.add_argument(
        "--cross-border-km",
        type=float,
        default=CROSS_BORDER_KM,
        help=(
            "Also measure distance to facilities up to this many km outside Dedza, "
            "so border cells are not marked unserved when a neighbouring district's "
            f"facility is close (default {CROSS_BORDER_KM:g}; 0 = Dedza facilities only)"
        ),
    )
    args = parser.parse_args()
    source_dir = args.source_dir.expanduser().resolve()
    cross_border_m = max(0.0, args.cross_border_km) * 1000
    facilities_path = source_dir / "Facilities" / "Facilities" / "facilities.shp"
    roads_path = source_dir / "hotosm_mwi_roads_osm_shp" / "roads_lines.shp"
    waterways_path = source_dir / "hotosm_mwi_waterways_osm_shp" / "waterways_lines.shp"
    missing = [
        path for path in (facilities_path, roads_path, waterways_path, *PLANNING_LAYER_PATHS)
        if not path.is_file()
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
    if n_fac == 0:
        con.close()
        raise RuntimeError("No health facilities fall inside Dedza; coverage analysis cannot be built.")

    # Facilities used for distance: Dedza's own plus any in neighbouring districts
    # within cross_border_m of the boundary (avoids a district-edge effect).
    con.execute(
        """
        CREATE OR REPLACE TABLE facilities_access AS
        SELECT f.name, f.lon, f.lat,
               ST_Intersects(ST_Point(f.lon, f.lat), d.geom) AS in_dedza,
               ST_Point(f.lon, f.lat) AS geom
        FROM facilities_all f, dedza d
        WHERE f.lon IS NOT NULL AND f.lat IS NOT NULL
          AND (
              ST_Intersects(ST_Point(f.lon, f.lat), d.geom)
              OR ST_DWithin(
                  ST_Transform(ST_Point(f.lon, f.lat), 'EPSG:4326', 'EPSG:32736'),
                  ST_Transform(d.geom, 'EPSG:4326', 'EPSG:32736'),
                  ?
              )
          )
        """,
        [cross_border_m],
    )
    n_access = con.execute("SELECT COUNT(*) FROM facilities_access").fetchone()[0]
    print(
        f"Facilities used for distance: {n_access} "
        f"({n_access - n_fac} outside Dedza within {cross_border_m / 1000:g} km)"
    )

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

    print(f"Buffering classified roads by {ROAD_ACCESS_KM:g} km...")
    road_buffer = json.loads(con.execute(
        f"""
        SELECT ST_AsGeoJSON(ST_Transform(
            ST_Buffer(ST_Collect(list(ST_Transform(r.geom, 'EPSG:4326', 'EPSG:32736'))), ?),
            'EPSG:32736', 'EPSG:4326'))
        FROM roads_clip r
        WHERE lower({highway_sql}) IN {ACCESS_ROADS}
        """,
        [ROAD_ACCESS_KM * 1000],
    ).fetchone()[0])

    print("Population (2020/2025/2030), road access and flood exposure per cell...")
    cell_rows_geo = con.execute(
        "SELECT cell_id, ST_AsGeoJSON(geom), ST_X(centroid), ST_Y(centroid) FROM cells"
    ).fetchall()
    grids = planning_grids(json.loads(boundary_geojson))
    indicators = cell_indicators(grids, [(cid, gj) for cid, gj, _, _ in cell_rows_geo], road_buffer)

    print("Walking distance and vehicle travel time from each cell's population centre to care...")
    origins = {
        int(cid): indicators[int(cid)]["pop_centre"] or (lon, lat)
        for cid, _, lon, lat in cell_rows_geo
    }
    access = network_access(
        origins,
        con.execute("SELECT ST_X(geom), ST_Y(geom) FROM facilities_access").fetchall(),
        PROCESSED / "clip.duckdb",
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE cell_planning (
            cell_id BIGINT, population_2020 DOUBLE, population DOUBLE, population_2030 DOUBLE,
            road_access_share DOUBLE, flood_area_share DOUBLE, flood_pop_share DOUBLE,
            travel_min DOUBLE, walk_m DOUBLE, origin_lon DOUBLE, origin_lat DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO cell_planning VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                cid, v["population_2020"], v["population"], v["population_2030"],
                v["road_access_share"], v["flood_area_share"], v["flood_pop_share"],
                access[cid]["vehicle_min"],
                None if access[cid]["walk_km"] is None else access[cid]["walk_km"] * 1000,
                origins[cid][0], origins[cid][1],
            )
            for cid, v in indicators.items()
        ],
    )
    population_totals = {year: sum(v[key] for v in indicators.values()) for year, key in (
        (2020, "population_2020"), (2025, "population"), (2030, "population_2030"))}
    print("Population in grid cells: " + ", ".join(
        f"{year}: {value:,.0f}" for year, value in population_totals.items()))

    con.execute(
        """
        CREATE OR REPLACE TABLE cell_stats AS
        WITH fac_dist AS (
            -- Straight-line distance from the same population centre that the
            -- walking distance starts from, kept for comparison.
            SELECT c.cell_id,
                   MIN(ST_Distance(
                        ST_Transform(ST_Point(p.origin_lon, p.origin_lat), 'EPSG:4326', 'EPSG:32736'),
                        ST_Transform(f.geom, 'EPSG:4326', 'EPSG:32736')
                   )) AS straight_dist_m
            FROM cells c
            JOIN cell_planning p USING (cell_id)
            CROSS JOIN facilities_access f
            GROUP BY c.cell_id
        ),
        fac_count AS (
            SELECT c.cell_id,
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
            -- Walking distance along roads and paths; straight line only if the
            -- population centre is on a fragment not connected to any facility.
            COALESCE(p.walk_m, f.straight_dist_m) AS dist_facility_m,
            f.straight_dist_m,
            COALESCE(r.road_km, 0) AS road_km,
            COALESCE(w.water_km, 0) AS water_km,
            COALESCE(fc.facilities_in_cell, 0) AS facilities_in_cell,
            p.population_2020,
            p.population,
            p.population_2030,
            p.population_2030 - p.population AS growth,
            p.road_access_share,
            p.flood_area_share,
            p.flood_pop_share,
            p.travel_min,
            c.geom
        FROM cells c
        LEFT JOIN fac_dist f USING (cell_id)
        LEFT JOIN fac_count fc USING (cell_id)
        LEFT JOIN road_len r USING (cell_id)
        LEFT JOIN water_len w USING (cell_id)
        LEFT JOIN cell_planning p USING (cell_id)
        """
    )

    pop_full, growth_full = con.execute(
        "SELECT quantile_cont(population_2030, ?), quantile_cont(growth, ?) FROM cell_stats",
        [FULL_SCORE_PERCENTILE, FULL_SCORE_PERCENTILE],
    ).fetchone()
    print(f"Full-score scales: population 2030 {pop_full:,.0f}, growth {growth_full:,.0f}")
    con.execute(
        """
        CREATE OR REPLACE TABLE cell_analysis AS
        WITH classified AS (
            SELECT *,
                   CASE
                       WHEN dist_facility_m IS NULL THEN 'unserved'
                       WHEN dist_facility_m <= 5000 THEN 'served'
                       WHEN dist_facility_m <= 8000 THEN 'underserved'
                       ELSE 'unserved'
                   END AS coverage,
                   CASE
                       WHEN road_km >= 4 THEN 'good'
                       WHEN road_km >= 1.5 THEN 'limited'
                       ELSE 'poor'
                   END AS road_access,
                   CASE
                       WHEN water_km >= 2 THEN 'good'
                       WHEN water_km >= 0.5 THEN 'limited'
                       ELSE 'poor'
                   END AS water_access
            FROM cell_stats
        )
        SELECT *,
               (
                   LEAST(population_2030 / $pop_full, 1.0) * $w_population
                   + LEAST(GREATEST(growth, 0) / $growth_full, 1.0) * $w_growth
                   + LEAST(COALESCE(dist_facility_m, $dist_full) / $dist_full, 1.0) * $w_distance
                   + road_access_share * $w_road
                   + (1 - flood_area_share) * $w_flood
               ) * CASE WHEN coverage = 'served' THEN $served_multiplier ELSE 1.0 END AS priority_score
        FROM classified
        """,
        {
            "pop_full": float(pop_full),
            "growth_full": float(growth_full),
            "dist_full": DISTANCE_FULL_M,
            "served_multiplier": SERVED_MULTIPLIER,
            **{f"w_{key}": value for key, value in PRIORITY_WEIGHTS.items()},
        },
    )

    stats = con.execute(
        """
        SELECT
            COUNT(*) AS n_cells,
            SUM(CASE WHEN coverage = 'served' THEN 1 ELSE 0 END) AS served,
            SUM(CASE WHEN coverage = 'underserved' THEN 1 ELSE 0 END) AS underserved,
            SUM(CASE WHEN coverage = 'unserved' THEN 1 ELSE 0 END) AS unserved,
            AVG(dist_facility_m) AS avg_dist,
            SUM(area_km2) AS area
        FROM cell_analysis
        """
    ).fetchone()
    print("Grid stats:", stats)

    print("Exporting layers to SQLite...")
    backup_path = DB_PATH.with_suffix(".sqlite.bak")
    if DB_PATH.exists():
        # Keep the previous database: it holds tables from the other import
        # scripts, which are copied into the new database below.
        DB_PATH.replace(backup_path)
        print(f"Previous database kept as {backup_path.name}")
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
            straight_dist_m REAL,
            road_km REAL,
            water_km REAL,
            facilities_in_cell INTEGER,
            population_2020 REAL,
            population REAL,
            population_2030 REAL,
            growth REAL,
            road_access_share REAL,
            flood_area_share REAL,
            flood_pop_share REAL,
            travel_min REAL,
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
            population REAL,
            population_2030 REAL,
            growth REAL,
            dist_facility_km REAL,
            travel_min REAL,
            road_access_share REAL,
            flood_area_share REAL,
            priority_score REAL,
            action TEXT
        );
        CREATE TABLE boundary (
            name TEXT,
            geojson TEXT
        );
        CREATE TABLE distance_facilities (
            id INTEGER PRIMARY KEY,
            name TEXT,
            lon REAL,
            lat REAL,
            in_dedza INTEGER
        );
        """
    )

    cur.execute(
        "INSERT INTO boundary VALUES (?, ?)",
        ("Dedza", json.dumps(json.loads(boundary_geojson))),
    )

    cur.executemany(
        "INSERT INTO distance_facilities (name, lon, lat, in_dedza) VALUES (?, ?, ?, ?)",
        con.execute("SELECT name, lon, lat, in_dedza FROM facilities_access ORDER BY name").fetchall(),
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

    cell_columns = (
        "cell_id", "lon", "lat", "area_km2", "dist_facility_m", "straight_dist_m", "road_km", "water_km",
        "facilities_in_cell", "population_2020", "population", "population_2030", "growth",
        "road_access_share", "flood_area_share", "flood_pop_share", "travel_min",
        "coverage", "road_access", "water_access", "priority_score",
    )
    cell_rows = con.execute(
        f"SELECT {', '.join(cell_columns)}, ST_AsGeoJSON(geom) FROM cell_analysis ORDER BY cell_id"
    ).fetchall()
    cur.executemany(
        f"INSERT INTO cells ({', '.join(cell_columns)}, geojson) "
        f"VALUES ({', '.join('?' * (len(cell_columns) + 1))})",
        cell_rows,
    )

    # Each action is generated from the cell's own calculated indicators.
    recommendation_rows = con.execute(
        """
        WITH ranked AS (
            SELECT
                ROW_NUMBER() OVER (ORDER BY priority_score DESC, cell_id DESC) AS rank,
                *,
                CASE
                    WHEN dist_facility_m IS NULL OR dist_facility_m > 8000
                        THEN 'Site a new health post or health centre'
                    WHEN dist_facility_m > 5000
                        THEN 'Extend outreach / mobile clinic coverage'
                    ELSE ''
                END AS health_action,
                CASE WHEN growth >= 1 THEN format(
                    'Plan capacity for {:,} more residents by 2030 (+{:.0f}%)',
                    CAST(ROUND(growth) AS BIGINT), growth / NULLIF(population, 0) * 100
                ) ELSE '' END AS growth_action,
                CASE WHEN road_access_share < 0.5 THEN format(
                    'Only {:.0f}% of residents live within {} km of a classified road: pair the site with feeder-road access',
                    road_access_share * 100, $road_km
                ) ELSE '' END AS road_action,
                CASE WHEN flood_area_share >= 0.05 THEN format(
                    '{:.0f}% of the cell is in the 1-in-100-year flood zone: choose a site outside it',
                    flood_area_share * 100
                ) ELSE '' END AS flood_action
            FROM cell_analysis
            WHERE area_km2 >= $min_km2
        )
        SELECT
            rank, cell_id, lon, lat, coverage,
            ROUND(population), ROUND(population_2030), ROUND(growth),
            ROUND(dist_facility_m / 1000.0, 2), ROUND(travel_min, 1),
            ROUND(road_access_share, 3), ROUND(flood_area_share, 3), ROUND(priority_score, 3),
            CASE
                WHEN health_action = '' AND growth_action = '' AND road_action = '' AND flood_action = ''
                    THEN 'Monitor service quality and staffing'
                ELSE CONCAT_WS(
                    '; ',
                    NULLIF(health_action, ''),
                    NULLIF(growth_action, ''),
                    NULLIF(road_action, ''),
                    NULLIF(flood_action, '')
                )
            END AS action
        FROM ranked
        WHERE rank <= 12
        ORDER BY rank
        """,
        {"min_km2": MIN_RANKED_CELL_KM2, "road_km": f"{ROAD_ACCESS_KM:g}"},
    ).fetchall()
    cur.executemany(
        """INSERT INTO recommendations (
            rank, cell_id, lon, lat, coverage, population, population_2030, growth,
            dist_facility_km, travel_min, road_access_share, flood_area_share,
            priority_score, action
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        recommendation_rows,
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
        "sources": SOURCES,
        "n_facilities_for_distance": str(n_access),
        "cross_border_km": f"{cross_border_m / 1000:g}",
        "min_ranked_cell_km2": f"{MIN_RANKED_CELL_KM2:g}",
        "distance_method": (
            "walking network distance along OSM roads, tracks and paths from each cell's "
            "population centre to the nearest facility (straight walk to the nearest path node)"
        ),
        "walking_speed_kmh": "5",
        **{f"weight_{key}": f"{value * 100:g}" for key, value in PRIORITY_WEIGHTS.items()},
        "full_score_percentile": f"{FULL_SCORE_PERCENTILE:g}",
        "population_full_score": f"{pop_full:.1f}",
        "growth_full_score": f"{growth_full:.1f}",
        "distance_full_m": f"{DISTANCE_FULL_M:g}",
        "served_multiplier": f"{SERVED_MULTIPLIER:g}",
        "road_access_km": f"{ROAD_ACCESS_KM:g}",
        "flood_return_period_years": "100",
        **{f"population_{year}": f"{value:.0f}" for year, value in population_totals.items()},
    }
    for k, v in meta.items():
        cur.execute("INSERT INTO meta VALUES (?,?)", (k, v))
    sql.commit()

    carried = carry_over_imported_tables(sql, backup_path)
    sql.close()
    print("Wrote", DB_PATH)
    if carried:
        print("Carried over imported tables:", ", ".join(carried))
    if "population_summary" in carried:
        print(
            "Coverage classes may have changed: run `python scripts/import_worldpop.py` "
            "to refresh population_summary."
        )


def carry_over_imported_tables(sql: sqlite3.Connection, backup_path: Path) -> list[str]:
    """Copy tables written by the other import scripts from the previous database."""
    if not backup_path.exists():
        return []
    carried = []
    sql.execute("ATTACH DATABASE ? AS old", (str(backup_path),))
    try:
        for table in IMPORTED_TABLES:
            row = sql.execute(
                "SELECT sql FROM old.sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if not row:
                continue
            sql.execute(row[0])  # same schema, created in the new main database
            sql.execute(f'INSERT INTO main."{table}" SELECT * FROM old."{table}"')
            carried.append(table)
        sql.commit()
    finally:
        sql.execute("DETACH DATABASE old")
    return carried


if __name__ == "__main__":
    main()
