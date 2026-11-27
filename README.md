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

## Project structure

```
app.py                  Starts the Flask app (python app.py)
sdss/                   Backend package
  config.py             File paths and fixed settings
  geo.py                Coordinate helpers
  data.py               SQLite database, cached map layers, site checks
  routing.py            Road network graph, shortest paths, route options
  walking.py            Walking distance to services along roads and paths
  scenario.py           Population and road-time estimates for a candidate site
  planning.py           Per-settlement planning figures, priority scales, suggested sites
  tiles.py              PNG map tiles for the environmental rasters
  routes/               API endpoints (Flask blueprints)
    layers.py           Map data: boundaries, facilities, schools, settlements, tiles
    analysis.py         Summaries, health-gap thresholds, priority scoring
    sites.py            Routing and candidate-site tools
scripts/                Data import and database build scripts
static/
  index.html            The page
  css/                  Styles, loaded in this order:
    base.css            Colours, fonts, buttons, icons
    shell.css           Top bar, tool rail, tool panel
    controls.css        Inputs, dropdowns, sliders, layer switches
    map.css             Map controls, legend, markers, popups, notifications
    results.css         Candidate-site and route cards over the map
    insights.css        Insights drawer: tabs, tables, briefing
    responsive.css      Tablet and phone layout
    print.css           Print / Save PDF
  js/                   Frontend (ES modules, no build step)
    main.js             Entry point
    app.js              Start-up order
    data.js, state.js   Loaded data and the user's current choices
    map/map.js          Leaflet map, layers, legend
    ui/                 Reusable widgets (dropdown, popups, notifications, shell)
    features/           One file per tool or panel (routes, sites, gap, priority, ...)
tests/                  pytest suite (python -m pytest)
qgis_plugin/            QGIS Python plugin for loading the project's GIS layers
```

## QGIS Python plugin

The optional Python plugin in `qgis_plugin/` loads the existing SQLite vector
layers, source GeoJSON boundaries and every available GeoTIFF into grouped QGIS
layers. See [qgis_plugin/README.md](qgis_plugin/README.md) for installation and
use. The Flask and Leaflet app remains the main project workflow and runs with
`python app.py`.

Each module starts with a comment saying what it does. A new API endpoint goes in the matching file under `sdss/routes/`; a new tool usually needs a `static/js/features/` module, wired up in `static/js/app.js`.

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

Restart the app after importing. At startup it prepares the road network in the background ("Road network and scenario baselines ready." in the terminal) so the first site comparison is fast. A fresh clone uses the committed Dedza road geometries if `data/processed/clip.duckdb` is absent; that fallback keeps map and settlement layers usable. Rebuild from Lab 4 source files for full cross-border routing and the complete walking network used by travel analysis.

Import the population-growth and flood-hazard layers (needed by the build; the clipped outputs are committed in `data/raw/`):

```powershell
python scripts/import_planning_layers.py
```

This downloads WorldPop R2025A constrained population for 2020 and 2030 (the 2030 raster is WorldPop's projection; CC BY 4.0) and the JRC Copernicus EMS GloFAS global river flood hazard map v2.1 tile for a 1-in-100-year flood, with its permanent-water layer (about 90 m; ODbL 1.0), and clips them to Dedza. The flood map is a global model, not an official national flood map.

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
- **Suggest best sites:** a greedy maximal-coverage search over the GRID3 settlements proposes the 3 locations that would bring the most currently unserved people, weighted by their projected 2030 population, within the access radius as walking distance. Only settlements that pass the site checks are candidate locations, and points are nudged off detected buildings.
- **Site checks** (`/api/site-check`, and on every candidate card) sample real layers at the point: depth in the JRC 1-in-100-year flood zone and permanent water, distance to the nearest classified road, terrain slope and WorldCover land cover. Each is reported as pass, warn or fail with the measured value; a site with any fail is not suitable as placed.
- Compare proposed or suggested school or health-centre sites using population reached now and by 2030, settlement road-time access, the site checks and—when imported—detected building footprints. Suitable sites rank first, then by 2030 population newly reached; each card warns when an existing facility (MHFR registry, any owner) is close enough to be duplicated.
- The Briefing tab turns the analysis into **recommended actions**: which TA to prioritise, the best health-centre location, the largest remote settlements for outreach, and whether new schools or school capacity is the real need. They are also included in the CSV export.
- Each candidate site is drawn with its actual walking-reach area (the area within the radius along roads and paths, using the same rule as the population count), and the *5 km walking access area* layer (`/api/health-walking-area`) shows where in Dedza a baseline facility is within a 5 km walk.
- View the flood-zone, land-cover and terrain-slope layers, and export a selected-area CSV brief.

## Database design

The app uses two SQLite databases. `data/processed/dedza.sqlite` is committed. `scripts/build_db.py` generates its analysis tables with DuckDB Spatial, and the import scripts add the reference layers. Geometries are stored as GeoJSON text in WGS 84 (EPSG:4326). Distances and lengths are calculated in a projected CRS during the build.

| Table | Rows | Purpose | Key columns |
|---|---|---|---|
| `boundary` | 1 | Dedza district polygon | `name`, `geojson` |
| `cells` | 486 | Analysis grid: one row per cell with its spatial-analysis results | `cell_id` (PK), `area_km2`, `dist_facility_m` (walking), `straight_dist_m`, `road_km`, `water_km`, `facilities_in_cell`, `population_2020`, `population`, `population_2030`, `growth`, `road_access_share`, `flood_area_share`, `flood_pop_share`, `travel_min`, `coverage`, `road_access`, `water_access`, `priority_score`, `geojson` |
| `recommendations` | 12 | Top-ranked cells, ranked in SQL (border slivers under 1 km² excluded), with actions generated from the cell's figures | `rank` (PK), `cell_id` → `cells.cell_id`, `population`, `population_2030`, `growth`, `dist_facility_km`, `travel_min`, `road_access_share`, `flood_area_share`, `priority_score`, `action` |
| `facilities` | 34 | MOH baseline health facilities inside Dedza (points shown on the map) | `id` (PK), `name`, `facility_type`, `owner`, `lon`, `lat` |
| `distance_facilities` | 70 | All facilities that distances are measured to (Dedza's plus those within 20 km across the border) | `id` (PK), `name`, `lon`, `lat`, `in_dedza` |
| `mhfr_facilities` | 52 | Operational facilities from the official MHFR registry (reference layer, incl. private and CHAM) | `source_id` (PK), `facility_code`, `name`, `facility_type`, `ownership`, `lon`, `lat` |
| `schools` | 286 | School points (coordinates corrected) | `id` (PK), `name`, `school_id`, `status`, `lon`, `lat` |
| `settlements` | 1,159 | GRID3 settlement extents with WorldPop population | `id` (PK), `name`, `place`, `population`, `adm3_name`, `lon`, `lat`, `building_count`, `source`, `source_id` |
| `osm_places` | 71 | Named OSM places, used only to label settlements | `id` (PK), `name`, `place`, `lon`, `lat` |
| `roads` | 7,390 | OSM road lines; also the routing network | `id` (PK), `highway`, `name`, `geojson` |
| `waterways` | 232 | OSM waterway lines | `id` (PK), `waterway`, `name`, `geojson` |
| `population_summary` | 50 | WorldPop 2025 zonal totals per area and coverage class | (`area_name`, `coverage`, `source`, `year`) composite PK, `population_est` |
| `meta` | 34 | Build parameters and provenance (thresholds, cross-border distance, priority weights and score scales, population totals, sources) | `key` (PK), `value` |

`data/processed/site_constraints.sqlite` is optional and not committed. `scripts/import_buildings.py` generates it, and its `buildings` table holds the detected footprints used to check candidate sites.

Processing pipeline: source shapefiles and rasters → DuckDB Spatial (clip to boundary, project, road and waterway length per cell, 2 km classified-road buffer, coverage classification, priority score, ranking) plus raster zonal statistics and road-network analysis in `scripts/planning_analysis.py` and `scripts/road_network.py` (population 2020/2025/2030, road access, flood exposure, walking distance and motorcycle travel time to care) → SQLite tables → Flask JSON API (`/api/...`) → Leaflet map and Turf.js in the browser. Leaflet 1.9.4 and Turf.js are bundled in `static/vendor/`, so the app runs without internet; only the OpenStreetMap base tiles need a connection.

## Data and limitations

The app combines MOH Malawi baseline facilities, the [Malawi Health Facility Registry](https://zipatala.health.gov.mw) (Ministry of Health), the Malawi schools dataset, [GRID3 settlement extents v3.0](https://data.humdata.org/dataset/grid3-mwi-settlement-extents-v3-0), HOT OSM roads/waterways/place names, geoBoundaries district boundaries, Traditional Authority boundaries from [geoBoundaries gbHumanitarian ADM3](https://www.geoboundaries.org/) (National Statistics Office of Malawi via HDX, CC BY 3.0 IGO), [WorldPop 2025](https://hub.worldpop.org/geodata/summary?id=74255), [ESA WorldCover 2021](https://esa-worldcover.org/en/data-access), and [Mapzen Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) when imported. WorldPop and WorldCover are CC BY 4.0; GRID3 is CC BY-SA.

Results are preliminary screening estimates, not verified recommendations. Population estimates do not measure service capacity or actual uptake. Walking distances assume people can use every mapped road, track and footpath and walk straight to the nearest one; unmapped paths make real walks shorter, and rivers or steep ground without a mapped crossing make them longer. Road routes and travel times use mapped roads and assumed speeds; they are not turn-by-turn navigation and omit traffic, road condition, seasonal closures, and access time. Settlement populations are WorldPop estimates inside GRID3 extents (about 704,000 of 1.02 million people); the rest live in hamlets and dispersed homesteads, which are not listed as settlements. Settlement names are borrowed from the nearest OSM place and are approximate.

Building footprints may be incomplete or out of date and do not establish land ownership or vacancy. WorldCover does not verify parcels or wetlands, and the generalized slope layer is not suitable for engineering decisions. Water-point datasets do not currently cover Dedza (the Water Point Data Exchange returns no Dedza records), so mapped waterways remain a proxy. Flood risk uses a global river-flood model (JRC GloFAS, about 90 m) and does not include flash floods or local drainage. WorldPop's 2030 figures are projections and grow at similar rates in most cells. Sites and priority prompts require field verification; the app does not certify site suitability.

The database can be rebuilt when the source Lab 4 shapefiles are available. Put the Lab 4 source folders under `data\source` and run `python scripts/build_db.py`, or specify their location with `python scripts/build_db.py --source-dir "C:\path\to\LAB NO 4"`. The source directory should contain `Facilities\Facilities\facilities.shp`, `hotosm_mwi_roads_osm_shp\roads_lines.shp`, and `hotosm_mwi_waterways_osm_shp\waterways_lines.shp`.

During a rebuild, DuckDB Spatial performs the projected straight-line distance, road-length, and waterway-length calculations and classifies each analysis cell in SQL from the walking distances calculated on the road network. SQL also calculates cell priority scores, district coverage totals, and the ranked top-12 recommendations. The resulting analysis tables are exported to `dedza.sqlite`, which the Flask app queries to serve its API and map layers; the app does not recompute the facility-coverage or priority analysis in Python.

Distance to care is the **walking distance** from each cell's population-weighted centre along OpenStreetMap roads, tracks and footpaths at 5 km/h: a straight walk to the nearest mapped path, then the shortest route to the nearest facility (`scripts/road_network.py`). The straight-line distance from the same point is kept in `cells.straight_dist_m` for comparison. 13 cells (about 2,000 residents) sit on path fragments not connected to any facility and fall back to straight-line distance. School and health distances for settlements, candidate-site reach and site suggestions use the same walking network; travel times use a separate roads-only network at indicative motorcycle speeds.

In the current build, 161 cells (1,276 km², 34% of the district; about 505,000 residents) are within a 5 km walk of care, 148 cells (about 347,000 residents) are 5–8 km away and 177 cells (about 167,000 residents) are beyond 8 km. About half of Dedza's 1.02 million residents therefore live more than a 5 km walk from a baseline facility.

Nearest-facility distance includes MOH facilities in neighbouring districts within 20 km of the Dedza boundary (36 in the current build, 70 in total), so border cells are not marked unserved when a facility across the district line is close; this moves 18 cells (about 15,600 residents) out of "unserved". Only Dedza's 34 facilities are stored in `facilities` and drawn on the map. Change the buffer with `--cross-border-km`; `--cross-border-km 0` restores Dedza-only distances. Cells smaller than 1 km² (border slivers) are scored but not ranked as recommendations.

A rebuild keeps the previous database as `dedza.sqlite.bak` and carries over the `schools`, `settlements`, `osm_places`, `mhfr_facilities` and `population_summary` tables. Coverage classes can change, so run `python scripts/import_worldpop.py` afterwards to refresh `population_summary`.

The **Health gap** sliders run a parameterized SQLite query against the stored nearest-facility distances. They update map coverage classes and district cell/area totals without rerunning the spatial distance calculation. Population summaries and Traditional Authority priorities remain the baseline 5 km / 8 km analysis.

The **Priority weights** sliders (`/api/priority-scenario`) re-score every cell in a parameterized SQLite query with user-chosen weights for the five criteria below. Weights are normalised to sum to 100%, the served-cell multiplier follows the current Health gap served threshold, and the numbered top-12 markers and Priority cells table update. The baseline weights and score scales are stored in the `meta` table, which the app reads; the default reproduces the stored ranking exactly.

The priority score is a screening heuristic for siting new facilities, not a validated suitability model. Each criterion is scaled 0–1 from the cell's calculated indicators:

| Criterion | Indicator | Full score at | Baseline weight |
|---|---|---|---|
| Population | WorldPop 2030 projected residents | 90th percentile of cells | 30% |
| Growth | Residents added 2025–2030 | 90th percentile of cells | 15% |
| Distance | Walking distance along roads and paths to the nearest facility | 12 km (about 2.4 hours on foot), so cells beyond the 8 km unserved threshold are still ranked by how far they are | 30% |
| Road access | Share of 2025 residents within 2 km of a classified road (OSM trunk–tertiary) | 100% | 15% |
| Flood safety | Share of the cell outside the 1-in-100-year flood zone and permanent water | 100% | 10% |

Served cells receive a 0.35 multiplier. The recommendation actions are generated in SQL from each cell's own figures (distance band, residents added by 2030, road access below 50%, flood share of 5% or more). Each cell also stores the road travel time from its population-weighted centre to the nearest facility (walking to the nearest road at 5 km/h, then the quickest road route at indicative motorcycle speeds); it is shown alongside the score, not included in it. A build with no health facilities inside Dedza fails rather than treating missing distances as zero.
