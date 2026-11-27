"""File locations and fixed settings shared by the whole backend.

Other modules read these as ``config.NAME`` (not ``from config import NAME``)
so tests can point them at temporary files with ``monkeypatch``.
"""

from __future__ import annotations

from pathlib import Path

from scripts.planning_analysis import FLOOD_DEPTH_PATH, PERMANENT_WATER_PATH, POPULATION_PATHS  # noqa: F401 (re-exported)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
STATIC = ROOT / "static"

# Databases
DB_PATH = PROCESSED / "dedza.sqlite"
ROUTE_DB_PATH = PROCESSED / "clip.duckdb"
SITE_CONSTRAINT_DB_PATH = PROCESSED / "site_constraints.sqlite"

# Rasters
ENVIRONMENTAL_RASTER_PATHS = {
    "land_cover": PROCESSED / "environmental" / "land_cover.tif",
    "slope": PROCESSED / "environmental" / "slope.tif",
    "flood": FLOOD_DEPTH_PATH,
}
WORLDPOP_PATH = POPULATION_PATHS[2025]
WORLDPOP_2030_PATH = POPULATION_PATHS[2030]

# Boundaries
ADM2_PATH = RAW / "MWI_ADM2_simplified.geojson"
# Traditional Authority boundaries: geoBoundaries gbHumanitarian (NSO Malawi / HDX, CC BY 3.0 IGO).
# The earlier gbOpen ADM3 file carried other districts' TA names on Dedza's polygons.
ADM3_PATH = RAW / "MWI_ADM3_humanitarian.geojson"

# OSM classes counted as classified roads for site road access (as in build_db.py).
ACCESS_ROAD_CLASSES = (
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "primary_link", "secondary_link", "tertiary_link",
)

# Routing and scenario settings
ROUTE_MODES = ("walking", "bicycle", "vehicle")  # "vehicle" = motorcycle / ambulance
SCENARIO_TRAVEL_TIME_BANDS_MIN = (30, 60)
MAX_SCENARIO_TRAVEL_TIME_MIN = max(SCENARIO_TRAVEL_TIME_BANDS_MIN)
MAX_ROAD_SNAP_KM = 1.0
HEALTH_WALKING_AREA_KM = 5.0

# Priority scoring
PRIORITY_CRITERIA = ("population", "growth", "distance", "road", "flood")
# Matches MIN_RANKED_CELL_KM2 in scripts/build_db.py: border slivers are not ranked.
MIN_RANKED_CELL_KM2 = 1.0
