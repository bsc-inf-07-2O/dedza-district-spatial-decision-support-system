# Dedza SDSS QGIS plugin

This Python plugin loads the spatial data already in this repository into the
current QGIS project. It reads vector features from `data/processed/dedza.sqlite`,
source GeoJSON from `data/raw/`, and every GeoTIFF under `data/`. The loader
skips optional files that are absent and reports unreadable sources in the QGIS
message bar and log.

## Install from this checkout

In QGIS, open **Settings → User Profiles → Open Active Profile Folder**, then
copy the `dedza_sdss` folder from `qgis_plugin/` into that profile's
`python/plugins/` folder. Enable **Dedza District SDSS Layers** under
**Plugins → Manage and Install Plugins → Installed**. Restart QGIS, open the
project checkout in VS Code (or keep it in its current location), and use the
**Dedza SDSS → Load Dedza SDSS layers** menu item. On first use, select the
repository root folder; the plugin remembers that location for later runs.

To use a development copy without copying files, set `QGIS_PLUGINPATH` to this
repository's `qgis_plugin` directory before starting QGIS. QGIS searches that
directory for plugin folders.

The original web application remains the project entry point: from the
repository root run `python app.py`, then open <http://127.0.0.1:5000>. The
plugin is an additional Python workflow for viewing the same source layers in
QGIS; it does not replace the Flask app or the team's Leaflet interface.

## Layer groups

- **SDSS database:** district boundary, planning cells, recommendations,
  facilities, schools, settlements, roads, waterways, and registry/reference
  layers available in the committed SQLite database.
- **Source data:** all top-level GeoJSON files in `data/raw/`, including
  Traditional Authority boundaries.
- **Raster layers:** all `.tif` files under `data/`, including population,
  flood-depth and permanent-water rasters, plus optional processed rasters.

The plugin uses QGIS's Python API and Python's standard SQLite/JSON modules;
installing the web application's `.venv` dependencies is not needed for it.
