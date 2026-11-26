"""Flask API endpoints, grouped by what they serve.

- layers     map data: boundaries, facilities, schools, settlements, grid, tiles
- analysis   district summaries, health-gap thresholds and priority scoring
- sites      routing and candidate-site tools (screening, scenarios, suggestions)
"""

from flask import Flask

from .analysis import bp as analysis_bp
from .layers import bp as layers_bp
from .sites import bp as sites_bp


def register_routes(app: Flask) -> None:
    for blueprint in (layers_bp, analysis_bp, sites_bp):
        app.register_blueprint(blueprint)
