"""Backend for the Dedza District Spatial Decision Support System.

Modules, from the bottom up (each one only imports the ones above it):

- config      file paths and fixed settings
- geo         small coordinate helpers
- data        SQLite database, cached map layers and site checks
- routing     road network graph, shortest paths and route options
- walking     walking distance to services along roads, tracks and paths
- scenario    population and road-time estimates for a candidate site
- planning    per-settlement planning figures and suggested sites
- tiles       PNG map tiles for the environmental rasters
- routes/     the Flask API endpoints, grouped into blueprints
"""
