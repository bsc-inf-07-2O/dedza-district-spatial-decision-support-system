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

Import the schools from the supplied archive after the main database exists:

```powershell
python scripts/import_schools.py "C:\path\to\Dataset.zip"
```

Import Dedza settlement locations from OSM:

```powershell
python scripts/import_settlements.py "C:\path\to\hotosm_mwi_populated_places_osm_shp.zip"
```

Import the public health reference and WorldPop 2025 summaries:

```bash
python scripts/import_health2026.py
python scripts/import_worldpop.py
```

The health script queries the public ArcGIS FeatureServer and stores Dedza points separately from the original baseline. The WorldPop script requires `rasterio` (included in `requirements.txt`) and the GeoTIFF at `data/raw/mwi_pop_2025_CN_100m_R2025A_v1.tif`. Restart the app after importing.

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

- Filter the map and service statistics by Dedza district or ADM3 area.
- Review mapped school and health-service access, compare route options, and explore Traditional Authority priorities.
- Compare proposed school or health-centre sites using population estimates, settlement access, and—when imported—detected building footprints.
- View land-cover and terrain-slope layers for context, and export a selected-area CSV brief.

## Database design

The app uses two SQLite databases. `data/processed/dedza.sqlite` is committed. `scripts/build_db.py` generates its analysis tables with DuckDB Spatial, and the import scripts add the reference layers. Geometries are stored as GeoJSON text in WGS 84 (EPSG:4326). Distances and lengths are calculated in a projected CRS during the build.

| Table | Rows | Purpose | Key columns |
|---|---|---|---|
| `boundary` | 1 | Dedza district polygon | `name`, `geojson` |
| `cells` | 486 | Analysis grid: one row per cell with its spatial-analysis results | `cell_id` (PK), `area_km2`, `dist_facility_m`, `road_km`, `water_km`, `facilities_in_cell`, `coverage`, `road_access`, `water_access`, `priority_score`, `geojson` |
| `recommendations` | 12 | Top-ranked underserved cells, ranked in SQL | `rank` (PK), `cell_id` → `cells.cell_id`, `dist_facility_km`, `priority_score`, `action` |
| `facilities` | 34 | MOH baseline health facilities (points) | `id` (PK), `name`, `facility_type`, `owner`, `lon`, `lat` |
| `health_facilities_2026` | 29 | Malawi Health 2026 reference points, kept separate from the baseline | `source_id` (PK), `name`, `ownership`, `lon`, `lat` |
| `schools` | 280 | School points | `id` (PK), `name`, `school_id`, `status`, `lon`, `lat` |
| `settlements` | 71 | OSM populated places | `id` (PK), `name`, `place`, `population`, `adm3_name`, `lon`, `lat` |
| `roads` | 7,390 | OSM road lines; also the routing network | `id` (PK), `highway`, `name`, `geojson` |
| `waterways` | 232 | OSM waterway lines | `id` (PK), `waterway`, `name`, `geojson` |
| `population_summary` | 55 | WorldPop 2025 zonal totals per area and coverage class | (`area_name`, `coverage`, `source`, `year`) composite PK, `population_est` |
| `meta` | 16 | Build parameters and provenance (thresholds, sources, dates) | `key` (PK), `value` |

`data/processed/site_constraints.sqlite` is optional and not committed. `scripts/import_buildings.py` generates it, and its `buildings` table holds the detected footprints used to check candidate sites.

Processing pipeline: source shapefiles and rasters → DuckDB Spatial (clip to boundary, project, nearest-facility distance, road and waterway length per cell, coverage classification, priority score, ranking) → SQLite tables → Flask JSON API (`/api/...`) → Leaflet map and Turf.js in the browser.

## Data and limitations

The app combines MOH Malawi baseline facilities, a separate [Malawi Health 2026 reference layer](https://services6.arcgis.com/zOnyumh63cMmLBBH/ArcGIS/rest/services/Malawi_Health_2026/FeatureServer), supplied school data, HOT OSM settlements/roads/waterways, geoBoundaries, [WorldPop 2025](https://hub.worldpop.org/geodata/summary?id=74255), [ESA WorldCover 2021](https://esa-worldcover.org/en/data-access), and [Mapzen Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) when imported. WorldPop and WorldCover are CC BY 4.0. Confirm the health-reference service's license before operational use.

Results are preliminary screening estimates, not verified recommendations. Population estimates do not measure service capacity or actual uptake. Road routes and travel times use mapped roads and assumed speeds; they are not turn-by-turn navigation and omit traffic, road condition, seasonal closures, and access time. Settlement points are not population totals—only two of 71 mapped settlements have an OSM population tag.

Building footprints may be incomplete or out of date and do not establish land ownership or vacancy. WorldCover does not verify parcels or wetlands, and the generalized slope layer is not suitable for engineering decisions. Water-point datasets do not currently cover Dedza, and the app does not assess flood risk. Sites and priority prompts require field verification; the app does not certify site suitability.

The database can be rebuilt when the source Lab 4 shapefiles are available. Put the Lab 4 source folders under `data\source` and run `python scripts/build_db.py`, or specify their location with `python scripts/build_db.py --source-dir "C:\path\to\LAB NO 4"`. The source directory should contain `Facilities\Facilities\facilities.shp`, `hotosm_mwi_roads_osm_shp\roads_lines.shp`, and `hotosm_mwi_waterways_osm_shp\waterways_lines.shp`.

During a rebuild, DuckDB Spatial performs the projected nearest-facility, road-length, and waterway-length calculations and classifies each analysis cell in SQL. SQL also calculates cell priority scores, district coverage totals, and the ranked top-12 recommendations. The resulting analysis tables are exported to `dedza.sqlite`, which the Flask app queries to serve its API and map layers; the app does not recompute the facility-coverage or priority analysis in Python.

The **Health gap what-if** sliders run a parameterized SQLite query against the stored nearest-facility distances. They update map coverage classes and district cell/area totals without rerunning the spatial distance calculation. Population summaries, Traditional Authority priorities, and priority scores remain the baseline 5 km / 8 km analysis.

The baseline priority score is a screening heuristic, not a validated suitability model: distance contributes 55% (capped at 16 km and normalized to 8 km), road length contributes 25% (capped at 8 km), and mapped-waterway length contributes 20% (capped at 4 km); served cells receive a 0.35 multiplier. Road-access bands (4 km and 1.5 km) and waterway-length bands (2 km and 0.5 km) are also assumptions. Waterway length is only a mapped-waterway proxy, not water-point or borehole access. A build with no health facilities inside Dedza fails rather than treating missing distances as zero.
