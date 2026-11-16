"""Import the public Malawi Health 2026 FeatureServer, clipped to Dedza."""

from __future__ import annotations

import json
import sqlite3
import urllib.request

import duckdb

from import_schools import ADM2_PATH, DB_PATH

SERVICE_URL = (
    "https://services6.arcgis.com/zOnyumh63cMmLBBH/ArcGIS/rest/services/"
    "Malawi_Health_2026/FeatureServer/0/query?where=1%3D1&outFields=*"
    "&returnGeometry=true&outSR=4326&f=geojson"
)


def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit("Dedza database is missing; build it before importing facilities.")
    if not ADM2_PATH.is_file():
        raise SystemExit(f"Dedza district boundary is missing: {ADM2_PATH}")

    with urllib.request.urlopen(SERVICE_URL, timeout=60) as response:
        payload = json.load(response)
    features = payload.get("features", [])
    rows = [
        (
            int(feature["properties"]["FID"]),
            feature["properties"].get("organisati") or "Unnamed facility",
            feature["properties"].get("orgunitlev") or "",
            feature["properties"].get("orgunitl_1") or "",
            feature["properties"].get("orgunitl_2") or "",
            float(feature["geometry"]["coordinates"][0]),
            float(feature["geometry"]["coordinates"][1]),
        )
        for feature in features
        if feature.get("geometry")
        and feature["geometry"].get("type") == "Point"
        and feature["geometry"].get("coordinates")
    ]

    spatial = duckdb.connect()
    try:
        spatial.execute("INSTALL spatial;")
        spatial.execute("LOAD spatial;")
        spatial.execute("SET geometry_always_xy = true;")
        spatial.execute(
            "CREATE TABLE incoming_health (source_id BIGINT, name VARCHAR, "
            "ownership VARCHAR, zone VARCHAR, dho VARCHAR, lon DOUBLE, lat DOUBLE)"
        )
        spatial.executemany("INSERT INTO incoming_health VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        dedza_rows = spatial.execute(
            """
            SELECT h.source_id, h.name, h.ownership, h.zone, h.dho, h.lon, h.lat
            FROM incoming_health h
            CROSS JOIN ST_Read(?) AS d
            WHERE lower(d.shapeName) = 'dedza'
              AND ST_Intersects(ST_Point(h.lon, h.lat), d.geom)
            ORDER BY h.name
            """,
            [str(ADM2_PATH)],
        ).fetchall()
    finally:
        spatial.close()

    with sqlite3.connect(DB_PATH) as database:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS health_facilities_2026 (
                source_id INTEGER PRIMARY KEY,
                name TEXT,
                ownership TEXT,
                zone TEXT,
                dho TEXT,
                lon REAL NOT NULL,
                lat REAL NOT NULL
            )
            """
        )
        database.execute("DELETE FROM health_facilities_2026")
        database.executemany(
            """
            INSERT INTO health_facilities_2026
                (source_id, name, ownership, zone, dho, lon, lat)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            dedza_rows,
        )
    print(
        f"Imported {len(dedza_rows)} of {len(rows)} public health points inside Dedza "
        "as a separate 2026 source layer."
    )


if __name__ == "__main__":
    main()