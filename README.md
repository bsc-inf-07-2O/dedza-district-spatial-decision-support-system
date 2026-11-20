# Dedza District Spatial Decision Support System

DD-SDSS is a Flask and Leaflet web GIS for preliminary public-service access screening in Dedza District, Malawi. Spatial operations use the locally bundled Turf.js 7.4.0; its MIT license is included in `static/vendor/`.

## Run

From PowerShell:

```powershell
cd "$HOME\Desktop\dedza-district-spatial-decision-support-system"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 and leave the terminal running. On first TA-boundary load, DuckDB may need internet access to install its spatial extension.

Import the reference layers after the main database exists (in this order):

```powershell
python scripts/import_schools.py "C:\path\to\Dataset.zip"            # or the mw_schools.shp file
python scripts/import_osm_places.py "C:\path\to\hotosm_mwi_populated_places_osm_shp.zip"
python scripts/import_grid3_settlements.py "C:\path\to\GRID3_MWI_settlement_extents_v3_0.gpkg"
python scripts/import_mhfr.py
python scripts/import_worldpop.py
```

- **Schools:** the source `.prj` wrongly declares the Hu Tzu Shan (Taiwan) datum; the script reads the coordinates as WGS 84 instead of shifting every school ~370 m, and normalises the misspelled status values (Dedza has no status recorded).
- **Settlements:** [GRID3 MWI Settlement Extents v3.0](https://data.humdata.org/dataset/grid3-mwi-settlement-extents-v3-0) (CIESIN, CC BY-SA; download the GeoPackage zip and extract it). Built-up and small settlement areas (50+ buildings) are kept: 1,159 settlements, each with a WorldPop population estimate and a name from the nearest OSM place (`osm_places`).
- **MHFR:** queries the public API of the Ministry of Health's [Malawi Health Facility Registry](https://zipatala.health.gov.mw) and stores operational Dedza facilities with ownership.
- **WorldPop:** requires `rasterio` and `data/raw/mwi_pop_2025_CN_100m_R2025A_v1.tif`.

Restart the app after importing. At startup it prepares the road network in the background ("Road network and scenario baselines ready." in the terminal) so the first site comparison is fast.

Import the public environmental rasters:

```powershell
python scripts/import_environmental_layers.py
```

This clips ESA WorldCover 2021 v200 land-cover classes to the existing Dedza boundary and downloads Mapzen Terrarium elevation tiles to derive an indicative slope raster. Outputs are written to `data/processed/environmental/`; the app serves them as map tiles. Internet access is required. WorldCover is CC BY 4.0. Mapzen Terrain Tiles combine global terrain sources at generalized resolution; the generated slope is for preliminary screening, not engineering or parcel-level analysis.

Import the detected building footprints for Dedza:

```powershell
python scripts/import_buildings.py
```

This downloads the Microsoft Global ML Building Footprints tiles whose tile bounds overlap Dedza, filters them against the district boundary and writes `data/processed/site_constraints.sqlite`. The current source index is dated 2026-08-13; expect roughly 162 MB of compressed tile downloads. The layer is licensed under CDLA Permissive 2.0. Restart the app after import. A detected footprint blocks a manually placed candidate point, but an absent footprint does not prove that the parcel is vacant.

## What the app does

- Filter the map and service statistics by Dedza district or Traditional Authority (TA / sub-chief area).
- Review mapped school and health-service access, compare route options, and explore Traditional Authority priorities.
- Re-run the health-gap thresholds and the priority-score weights live in SQL and watch the map and ranking update.
- **Suggest best sites:** a greedy maximal-coverage search over the GRID3 settlements proposes the 3 locations that would bring the most currently unserved people within the access radius, nudged off detected buildings.
- Compare proposed or suggested school or health-centre sites using population estimates, settlement access, and—when imported—detected building footprints; sites are ranked by people newly reached, the best is marked as recommended, and each card warns when an existing facility (MHFR registry, any owner) is close enough to be duplicated.
- The Briefing tab turns the analysis into **recommended actions**: which TA to prioritise, the best health-centre location, the largest remote settlements for outreach, and whether new schools or school capacity is the real need. They are also included in the CSV export.
- View land-cover and terrain-slope layers for context, and export a selected-area CSV brief.

## Database design

The app uses two SQLite databases. `data/processed/dedza.sqlite` is committed. `scripts/build_db.py` generates its analysis tables with DuckDB Spatial, and the import scripts add the reference layers. Geometries are stored as GeoJSON text in WGS 84 (EPSG:4326). Distances and lengths are calculated in a projected CRS during the build.

| Table | Rows | Purpose | Key columns |
|---|---|---|---|
| `boundary` | 1 | Dedza district polygon | `name`, `geojson` |
| `cells` | 486 | Analysis grid: one row per cell with its spatial-analysis results | `cell_id` (PK), `area_km2`, `dist_facility_m`, `road_km`, `water_km`, `facilities_in_cell`, `coverage`, `road_access`, `water_access`, `priority_score`, `geojson` |
| `recommendations` | 12 | Top-ranked cells, ranked in SQL (border slivers under 1 km² excluded) | `rank` (PK), `cell_id` → `cells.cell_id`, `dist_facility_km`, `priority_score`, `action` |
| `facilities` | 34 | MOH baseline health facilities inside Dedza (points shown on the map) | `id` (PK), `name`, `facility_type`, `owner`, `lon`, `lat` |
| `mhfr_facilities` | 52 | Operational facilities from the official MHFR registry (reference layer, incl. private and CHAM) | `source_id` (PK), `facility_code`, `name`, `facility_type`, `ownership`, `lon`, `lat` |
| `schools` | 286 | School points (coordinates corrected) | `id` (PK), `name`, `school_id`, `status`, `lon`, `lat` |
| `settlements` | 1,159 | GRID3 settlement extents with WorldPop population | `id` (PK), `name`, `place`, `population`, `adm3_name`, `lon`, `lat`, `building_count`, `source`, `source_id` |
| `osm_places` | 71 | Named OSM places, used only to label settlements | `id` (PK), `name`, `place`, `lon`, `lat` |
| `roads` | 7,390 | OSM road lines; also the routing network | `id` (PK), `highway`, `name`, `geojson` |
| `waterways` | 232 | OSM waterway lines | `id` (PK), `waterway`, `name`, `geojson` |
| `population_summary` | 50 | WorldPop 2025 zonal totals per area and coverage class | (`area_name`, `coverage`, `source`, `year`) composite PK, `population_est` |
| `meta` | 19 | Build parameters and provenance (thresholds, cross-border distance, sources) | `key` (PK), `value` |

`data/processed/site_constraints.sqlite` is optional and not committed. `scripts/import_buildings.py` generates it, and its `buildings` table holds the detected footprints used to check candidate sites.

Processing pipeline: source shapefiles and rasters → DuckDB Spatial (clip to boundary, project, nearest-facility distance, road and waterway length per cell, coverage classification, priority score, ranking) → SQLite tables → Flask JSON API (`/api/...`) → Leaflet map and Turf.js in the browser. Leaflet 1.9.4 and Turf.js are bundled in `static/vendor/`, so the app runs without internet; only the OpenStreetMap base tiles need a connection.

## Data and limitations

The app combines MOH Malawi baseline facilities, the [Malawi Health Facility Registry](https://zipatala.health.gov.mw) (Ministry of Health), the Malawi schools dataset, [GRID3 settlement extents v3.0](https://data.humdata.org/dataset/grid3-mwi-settlement-extents-v3-0), HOT OSM roads/waterways/place names, geoBoundaries district boundaries, Traditional Authority boundaries from [geoBoundaries gbHumanitarian ADM3](https://www.geoboundaries.org/) (National Statistics Office of Malawi via HDX, CC BY 3.0 IGO), [WorldPop 2025](https://hub.worldpop.org/geodata/summary?id=74255), [ESA WorldCover 2021](https://esa-worldcover.org/en/data-access), and [Mapzen Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) when imported. WorldPop and WorldCover are CC BY 4.0; GRID3 is CC BY-SA.

Results are preliminary screening estimates, not verified recommendations. Population estimates do not measure service capacity or actual uptake. Road routes and travel times use mapped roads and assumed speeds; they are not turn-by-turn navigation and omit traffic, road condition, seasonal closures, and access time. Settlement populations are WorldPop estimates inside GRID3 extents (about 704,000 of 1.02 million people); the rest live in hamlets and dispersed homesteads, which are not listed as settlements. Settlement names are borrowed from the nearest OSM place and are approximate.

Building footprints may be incomplete or out of date and do not establish land ownership or vacancy. WorldCover does not verify parcels or wetlands, and the generalized slope layer is not suitable for engineering decisions. Water-point datasets do not currently cover Dedza (the Water Point Data Exchange returns no Dedza records), so mapped waterways remain a proxy, and the app does not assess flood risk. Sites and priority prompts require field verification; the app does not certify site suitability.

The database can be rebuilt when the source Lab 4 shapefiles are available. Put the Lab 4 source folders under `data\source` and run `python scripts/build_db.py`, or specify their location with `python scripts/build_db.py --source-dir "C:\path\to\LAB NO 4"`. The source directory should contain `Facilities\Facilities\facilities.shp`, `hotosm_mwi_roads_osm_shp\roads_lines.shp`, and `hotosm_mwi_waterways_osm_shp\waterways_lines.shp`.

During a rebuild, DuckDB Spatial performs the projected nearest-facility, road-length, and waterway-length calculations and classifies each analysis cell in SQL. SQL also calculates cell priority scores, district coverage totals, and the ranked top-12 recommendations. The resulting analysis tables are exported to `dedza.sqlite`, which the Flask app queries to serve its API and map layers; the app does not recompute the facility-coverage or priority analysis in Python.

Nearest-facility distance includes MOH facilities in neighbouring districts within 20 km of the Dedza boundary (36 in the current build, 70 in total), so border cells are not marked unserved when a facility across the district line is close. Only Dedza's 34 facilities are stored in `facilities` and drawn on the map. Change the buffer with `--cross-border-km`; `--cross-border-km 0` restores Dedza-only distances. Cells smaller than 1 km² (border slivers) are scored but not ranked as recommendations.

A rebuild keeps the previous database as `dedza.sqlite.bak` and carries over the `schools`, `settlements`, `osm_places`, `mhfr_facilities` and `population_summary` tables. Coverage classes can change, so run `python scripts/import_worldpop.py` afterwards to refresh `population_summary`.

The **Health gap** sliders run a parameterized SQLite query against the stored nearest-facility distances. They update map coverage classes and district cell/area totals without rerunning the spatial distance calculation. Population summaries and Traditional Authority priorities remain the baseline 5 km / 8 km analysis.

The **Priority weights** sliders (`/api/priority-scenario`) re-score every cell in a parameterized SQLite query with user-chosen weights for distance, lack of road access and lack of mapped waterways. Weights are normalised to sum to 100%, the served-cell multiplier follows the current Health gap served threshold, and the numbered top-12 markers and Priority cells table update. The default 55 / 25 / 20 reproduces the stored baseline exactly.

The baseline priority score is a screening heuristic, not a validated suitability model: distance contributes 55% (capped at 16 km and normalized to 8 km), road length contributes 25% (capped at 8 km), and mapped-waterway length contributes 20% (capped at 4 km); served cells receive a 0.35 multiplier. Road-access bands (4 km and 1.5 km) and waterway-length bands (2 km and 0.5 km) are also assumptions. Waterway length is only a mapped-waterway proxy, not water-point or borehole access. A build with no health facilities inside Dedza fails rather than treating missing distances as zero.
