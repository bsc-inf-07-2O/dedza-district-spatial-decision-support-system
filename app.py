"""Dedza District facility-gap GIS web application.

Run with ``python app.py`` and open http://127.0.0.1:5000.
The backend code lives in the ``sdss`` package; see sdss/__init__.py for a map of it.
"""

from __future__ import annotations

import mimetypes

from flask import Flask, send_from_directory

from sdss import config
from sdss.planning import settlement_planning
from sdss.routes import register_routes
from sdss.routing import load_road_network
from sdss.scenario import district_population_pixels, scenario_baseline_road_times
from sdss.walking import health_walking_area

# The frontend uses ES modules, which browsers only run when served as JavaScript.
# Windows sometimes maps .js to text/plain in the registry, so set it explicitly.
mimetypes.add_type("text/javascript", ".js")

app = Flask(__name__, static_folder=str(config.STATIC), static_url_path="/static")
register_routes(app)


@app.get("/")
def index():
    return send_from_directory(config.STATIC, "index.html")


def warm_caches() -> None:
    """Prepare the road network and scenario baselines so the first site comparison is fast."""
    try:
        load_road_network()
        for facility_type in ("health", "school"):
            scenario_baseline_road_times(facility_type)
        settlement_planning()
        district_population_pixels()
        health_walking_area(config.HEALTH_WALKING_AREA_KM)
        print("Road network and scenario baselines ready.", flush=True)
    except Exception as error:  # the app still works; the first comparison is just slower
        print(f"Cache warm-up skipped: {error}", flush=True)


if __name__ == "__main__":
    import threading

    threading.Thread(target=warm_caches, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
