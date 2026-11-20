"""Import Dedza health facilities from the Malawi Health Facility Registry (MHFR).

MHFR (https://zipatala.health.gov.mw) is the Ministry of Health's official
registry. Its public API exposes facilities, districts, owners and
geolocations. Only facilities that are not archived, are listed with
operational status 1 (operational; this group includes Dedza District
Hospital), have coordinates and fall inside the Dedza boundary are kept.

The facility-type lookup needs authentication, so types are labelled from the
registry's numeric type ids, mapped by inspecting the Dedza names in each id
(for example id 8 = health centres, id 6 = dispensaries).

This layer is shown for reference and routing. The baseline coverage analysis
still uses the MOH facility shapefile in scripts/build_db.py, whose positions
match MHFR exactly for the facilities they share.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "dedza.sqlite"
BOUNDARY_PATH = ROOT / "data" / "raw" / "dedza_boundary.geojson"
API = "https://zipatala.health.gov.mw/api"

TYPE_LABELS = {
    1: "Health post",
    2: "Clinic",
    4: "District hospital",
    5: "Clinic",
    6: "Dispensary",
    7: "Hospital",
    8: "Health centre",
    9: "Clinic",
}
OPERATIONAL = 1


def fetch(path: str) -> list[dict]:
    request = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": "dd-sdss-import"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit("Dedza database is missing; build it before importing MHFR facilities.")
    boundary = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    dedza = unary_union([shape(feature["geometry"]) for feature in boundary["features"]])

    districts = fetch("districts")
    dedza_id = next(d["id"] for d in districts if d["district_name"].strip().lower() == "dedza")
    owners = {o["id"]: o["facility_owner"] for o in fetch("owners")}
    geolocations = {g["facility_id"]: g for g in fetch("geolocations")}

    rows = []
    for facility in fetch("facilities"):
        if facility["district_id"] != dedza_id or facility.get("archived_date"):
            continue
        if facility["facility_operational_status_id"] != OPERATIONAL:
            continue
        geo = geolocations.get(facility["id"])
        try:
            lon, lat = float(geo["longitude"]), float(geo["latitude"])
        except (TypeError, ValueError, KeyError):
            continue
        if not dedza.contains(Point(lon, lat)):
            continue
        rows.append((
            facility["id"],
            facility["facility_code"],
            " ".join(facility["facility_name"].split()),
            TYPE_LABELS.get(facility["facility_type_id"], "Health facility"),
            owners.get(facility["facility_owner_id"], "Unknown"),
            lon,
            lat,
        ))

    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS mhfr_facilities (
                source_id INTEGER PRIMARY KEY,
                facility_code TEXT,
                name TEXT NOT NULL,
                facility_type TEXT,
                ownership TEXT,
                lon REAL NOT NULL,
                lat REAL NOT NULL
            )
            """
        )
        con.execute("DELETE FROM mhfr_facilities")
        con.executemany("INSERT INTO mhfr_facilities VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        # Superseded by MHFR: the ArcGIS "Malawi Health 2026" copy duplicated the MOH list.
        con.execute("DROP TABLE IF EXISTS health_facilities_2026")
    print(f"Imported {len(rows)} operational MHFR facilities inside Dedza into {DB_PATH}")


if __name__ == "__main__":
    main()
