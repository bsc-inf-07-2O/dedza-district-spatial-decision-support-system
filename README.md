# Dedza District Spatial Decision Support System

DD-SDSS is a Flask and Leaflet web GIS for preliminary public-service access screening in Dedza District, Malawi.

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

Import the detected building footprints for Dedza:

```powershell
python scripts/import_buildings.py
```

This downloads the Microsoft Global ML Building Footprints tiles whose tile bounds overlap Dedza, filters them against the district boundary and writes `data/processed/site_constraints.sqlite`. The current source index is dated 2026-08-13; expect roughly 162 MB of compressed tile downloads. The layer is licensed under CDLA Permissive 2.0. Restart the app after import. A detected footprint blocks a manually placed candidate point, but an absent footprint does not prove that the parcel is vacant.

## Available analysis

- Search and select a Dedza ADM3 area; its boundary, map layers, statistics and priority cells are filtered to that area.
- Calculate each mapped settlement's nearest-school straight-line distance and count settlements inside an adjustable distance radius.
- Compare up to three distinct mapped-network choices from a selected settlement to a selected baseline or 2026-reference health facility: the shortest-distance route, the quickest estimated route, and a distance-based alternative where available. Routes are shown together in distinct styles; selecting one highlights it and displays its mapped-road distance, total distance, access connectors, estimated travel time, distance/time differences from the shortest route, and the share of road path common with the shortest route. Travel-time estimates use assumed speeds by mapped OSM road class.
- Rank Traditional Authorities by estimated WorldPop population in baseline health cells more than 8 km from a facility; show each TA's affected population share and allow selecting a row to filter the map. This is a screening priority, not a census count or a measure of facility capacity.
- Compare proposed school or health-centre sites using separate straight-line population and road-time measures. Estimate additional residents from WorldPop 2025 100 m pixels outside existing same-type facility catchments, and compare mapped settlements newly within 30- and 60-minute road-time bands versus existing baseline services. Show TA breakdowns and settlement names in the minimizable map overlay. When building footprints are imported, show them at close zoom and reject candidate points inside detected footprints. Facility capacity is marked unknown and is not scored; candidates are evaluated independently.
- Export a selected-area CSV brief with coverage summaries, all candidate-site estimates and their TA breakdowns, and settlement-level nearest-service distances.
- Compare 3 km cells using straight-line distance to mapped health facilities, road length and mapped waterway length.
- Review preliminary priority scores and planning prompts. The fixed score is a screening proxy, not a validated facility-suitability model.

The current data includes MOH Malawi baseline facilities, a separate [ArcGIS Malawi Health 2026 reference layer](https://services6.arcgis.com/zOnyumh63cMmLBBH/ArcGIS/rest/services/Malawi_Health_2026/FeatureServer), the supplied Malawi schools shapefile, HOT OSM settlement points, roads and waterways, geoBoundaries district boundaries, and [WorldPop 2025 gridded population estimates](https://hub.worldpop.org/geodata/summary?id=74255). Schools, settlements, health-reference points and population summaries are clipped or summarized to Dedza using administrative geometry. WorldPop is CC BY 4.0; the health service's data license/provenance should be confirmed before operational use. Neither the supplied WPDx CSV nor the public 2019–2020 flood-response water assessment contains points inside Dedza; mapped waterways do not represent water points.

## Data and method limits

Only two of the 71 mapped settlements have an OSM population tag, so settlement locations must not be interpreted as population totals. WorldPop estimates are assigned to existing 3 km health-coverage classes for the access summaries. Candidate-site population comparison sums 100 m pixels inside the same-radius, straight-line candidate zone that are not already inside an existing same-type facility zone; it is not a measure of facility capacity, service quality or actual uptake. Candidate road-time comparison calculates directed mapped-road travel times from settlements to the proposed site and nearest existing same-type baseline service. It reports settlements newly inside 30- and 60-minute bands, not population reachable in those times. It uses indicative motorcycle speeds by OSM road class (40 km/h motorway/trunk, 35 primary, 30 secondary, 25 tertiary, 20 residential/unclassified/living-street, 10 service/track/path and 15 other), excludes straight-line connector time, and omits candidate or settlement points more than 1 km from mapped roads. It does not account for traffic, road condition, stops, seasonal closures, actual travel mode or facility capacity. Candidate sites are evaluated independently. These are screening estimates and should be field-verified. Available water datasets have no Dedza water-point coverage, so water-source accessibility remains unmeasured. The route tool uses the clipped OSM network in `data/processed/clip.duckdb`; it is not turn-by-turn navigation or a validated travel-time estimate. It may use mapped paths and tracks, includes straight-line connectors between locations, and does not assess vehicle access, road condition, traffic or seasonal closures. A missing or disconnected road segment can prevent a route. Candidate cells are preliminary planning prompts and require field verification.

Building footprints are satellite-detected structures, not a land-tenure or parcel-occupancy register; dates and completeness vary. The building layer is not clipped geometrically at the district boundary, and edge records may be incomplete. Screening only rejects a candidate point that intersects a detected footprint; it does not assess the full land parcel or required building setbacks. No verified Dedza flood extent or susceptibility layer, DEM, or land-cover raster is currently installed. Flood exposure, slope and wetland constraints therefore remain unassessed. Do not treat a candidate outside mapped waterways or footprints as safe or vacant. The app does not automatically recommend or certify a site.

Route options are alternatives found in the clipped OSM network, not an enumeration of every possible path or turn-by-turn navigation. The quickest estimated route is calculated using indicative speeds: 40 km/h on motorway/trunk, 35 on primary, 30 on secondary, 25 on tertiary, 20 on residential/unclassified/living streets, 10 on service/track/path, and 15 on other mapped classes. These are assumptions, not locally validated motorcycle speeds. Time excludes traffic, stops, road condition, access connectors and seasonal closures.

The database can be rebuilt when the source Lab 4 shapefiles are available. Put the Lab 4 source folders under `data\source` and run `python scripts/build_db.py`, or specify their location with `python scripts/build_db.py --source-dir "C:\path\to\LAB NO 4"`. The source directory should contain `Facilities\Facilities\facilities.shp`, `hotosm_mwi_roads_osm_shp\roads_lines.shp`, and `hotosm_mwi_waterways_osm_shp\waterways_lines.shp`.
# dedza-district-spatial-decision-support-system
