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

## Data and limitations

The app combines MOH Malawi baseline facilities, a separate [Malawi Health 2026 reference layer](https://services6.arcgis.com/zOnyumh63cMmLBBH/ArcGIS/rest/services/Malawi_Health_2026/FeatureServer), supplied school data, HOT OSM settlements/roads/waterways, geoBoundaries, [WorldPop 2025](https://hub.worldpop.org/geodata/summary?id=74255), [ESA WorldCover 2021](https://esa-worldcover.org/en/data-access), and [Mapzen Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) when imported. WorldPop and WorldCover are CC BY 4.0. Confirm the health-reference service's license before operational use.

Results are preliminary screening estimates, not verified recommendations. Population estimates do not measure service capacity or actual uptake. Road routes and travel times use mapped roads and assumed speeds; they are not turn-by-turn navigation and omit traffic, road condition, seasonal closures, and access time. Settlement points are not population totals—only two of 71 mapped settlements have an OSM population tag.

Building footprints may be incomplete or out of date and do not establish land ownership or vacancy. WorldCover does not verify parcels or wetlands, and the generalized slope layer is not suitable for engineering decisions. Water-point datasets do not currently cover Dedza, and the app does not assess flood risk. Sites and priority prompts require field verification; the app does not certify site suitability.

The database can be rebuilt when the source Lab 4 shapefiles are available. Put the Lab 4 source folders under `data\source` and run `python scripts/build_db.py`, or specify their location with `python scripts/build_db.py --source-dir "C:\path\to\LAB NO 4"`. The source directory should contain `Facilities\Facilities\facilities.shp`, `hotosm_mwi_roads_osm_shp\roads_lines.shp`, and `hotosm_mwi_waterways_osm_shp\waterways_lines.shp`.
