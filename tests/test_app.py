import json
import sqlite3

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app import app
from scripts.road_network import haversine_km, motorcycle_speed_kmh
from scripts.site_constraints import point_in_geometry
from sdss import config, routing, walking
from sdss.data import building_at, db, load_payloads
from sdss.routing import calculate_road_route, project_road_node, shortest_road_time_to_target
from sdss.scenario import estimate_scenario_road_access, scenario_baseline_road_times
from sdss.schools import level_group, school_level


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert "database_ready" in payload


def test_homepage_uses_locally_served_turf_bundle(client):
    homepage = client.get("/")
    assert homepage.status_code == 200
    assert b"/static/vendor/turf-7.4.0.min.js" in homepage.data
    assert b"https://unpkg.com/@turf/turf" not in homepage.data

    bundle = client.get("/static/vendor/turf-7.4.0.min.js")
    assert bundle.status_code == 200
    assert bundle.data


def test_homepage_uses_locally_served_leaflet_for_offline_use(client):
    homepage = client.get("/")
    assert b"unpkg.com" not in homepage.data
    for path in (
        "/static/vendor/leaflet-1.9.4/leaflet.js",
        "/static/vendor/leaflet-1.9.4/leaflet.css",
    ):
        assert path.encode() in homepage.data
        response = client.get(path)
        assert response.status_code == 200
        assert response.data


def test_environmental_layer_status_and_tile_endpoint(client, tmp_path, monkeypatch):
    raster_path = tmp_path / "land_cover.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_origin(-20_000_000, 20_000_000, 20_000_000, 20_000_000),
        nodata=0,
    ) as dataset:
        dataset.write(np.array([[10, 40], [50, 80]], dtype=np.uint8), 1)
    monkeypatch.setattr(
        config,
        "ENVIRONMENTAL_RASTER_PATHS",
        {
            "land_cover": raster_path,
            "slope": tmp_path / "missing-slope.tif",
            "flood": tmp_path / "missing-flood.tif",
        },
    )
    monkeypatch.setattr(
        config, "SITE_CONSTRAINT_DB_PATH", tmp_path / "not-imported.sqlite"
    )

    status = client.get("/api/site-layers").get_json()
    assert status["land_cover"]["available"] is True
    assert status["terrain"]["available"] is False
    assert status["flood"]["available"] is False
    assert set(status) == {"buildings", "terrain", "land_cover", "flood"}

    response = client.get("/api/environmental-tiles/land_cover/0/0/0.png")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_environmental_tile_rejects_invalid_zoom(client, tmp_path, monkeypatch):
    raster_path = tmp_path / "land_cover.tif"
    raster_path.touch()
    monkeypatch.setattr(
        config,
        "ENVIRONMENTAL_RASTER_PATHS",
        {
            "land_cover": raster_path,
            "slope": tmp_path / "missing-slope.tif",
            "flood": tmp_path / "missing-flood.tif",
        },
    )

    response = client.get("/api/environmental-tiles/land_cover/19/0/0.png")

    assert response.status_code == 400
    assert "Zoom level" in response.get_json()["error"]


def test_summary_endpoint_returns_metrics(client):
    response = client.get("/api/summary")
    assert response.status_code == 200
    payload = response.get_json()
    assert "meta" in payload
    assert "type_counts" in payload
    assert "coverage" in payload
    assert payload["coverage"]


def test_gap_analysis_reclassifies_cells_and_aggregates_in_sql(
    client, tmp_path, monkeypatch
):
    database_path = tmp_path / "gap-analysis.sqlite"
    con = sqlite3.connect(database_path)
    con.execute(
        "CREATE TABLE cells (cell_id INTEGER, dist_facility_m REAL, area_km2 REAL)"
    )
    con.executemany(
        "INSERT INTO cells VALUES (?, ?, ?)",
        [
            (1, 3000, 1.0),
            (2, 5000, 2.0),
            (3, 8000, 3.0),
            (4, 10000, 4.0),
            (5, None, 5.0),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(config, "DB_PATH", database_path)

    response = client.get("/api/gap-analysis?served_km=3&underserved_km=8")

    assert response.status_code == 200
    result = response.get_json()
    assert result["served_km"] == 3
    assert result["underserved_km"] == 8
    assert result["coverage"] == {
        "served": {"cells": 1, "km2": 1.0},
        "underserved": {"cells": 2, "km2": 5.0},
        "unserved": {"cells": 2, "km2": 9.0},
    }
    assert result["cells"] == [
        {"cell_id": 1, "coverage": "served"},
        {"cell_id": 2, "coverage": "underserved"},
        {"cell_id": 3, "coverage": "underserved"},
        {"cell_id": 4, "coverage": "unserved"},
        {"cell_id": 5, "coverage": "unserved"},
    ]


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("served_km=abc", "numbers"),
        ("served_km=5&underserved_km=5", "Need"),
        ("served_km=nan&underserved_km=8", "Need"),
        ("served_km=5&underserved_km=51", "Need"),
    ],
)
def test_gap_analysis_rejects_invalid_thresholds(client, query, message):
    response = client.get(f"/api/gap-analysis?{query}")

    assert response.status_code == 400
    assert message in response.get_json()["error"]


def test_database_contains_sql_derived_coverage_and_ranked_recommendations():
    con = db()
    try:
        mismatched_cells = con.execute(
            """
            SELECT COUNT(*)
            FROM cells
            WHERE coverage != CASE
                WHEN dist_facility_m IS NULL THEN 'unserved'
                WHEN dist_facility_m <= 5000 THEN 'served'
                WHEN dist_facility_m <= 8000 THEN 'underserved'
                ELSE 'unserved'
            END
            """
        ).fetchone()[0]
        expected_recommendations = [
            row["cell_id"]
            for row in con.execute(
                """
                SELECT cell_id
                FROM cells
                WHERE area_km2 >= 1.0
                ORDER BY priority_score DESC, cell_id DESC
                LIMIT 12
                """
            )
        ]
        stored_recommendations = [
            row["cell_id"]
            for row in con.execute(
                "SELECT cell_id FROM recommendations ORDER BY rank"
            )
        ]
    finally:
        con.close()

    assert mismatched_cells == 0
    assert stored_recommendations == expected_recommendations


def test_priority_scenario_default_weights_reproduce_baseline(client):
    response = client.get("/api/priority-scenario")
    assert response.status_code == 200
    payload = response.get_json()

    con = db()
    try:
        stored = {
            row["cell_id"]: row["priority_score"]
            for row in con.execute("SELECT cell_id, priority_score FROM cells")
        }
        baseline_top = [
            row["cell_id"]
            for row in con.execute("SELECT cell_id FROM recommendations ORDER BY rank")
        ]
    finally:
        con.close()

    meta = load_payloads()["meta"]
    expected = {key: float(meta[f"weight_{key}"]) for key in ("population", "growth", "distance", "road", "flood")}
    assert payload["weights"] == pytest.approx(expected)
    assert payload["baseline_weights"] == expected
    assert len(payload["cells"]) == len(stored)
    for cell in payload["cells"]:
        assert cell["score"] == pytest.approx(stored[cell["cell_id"]], abs=0.0006)
    assert payload["top_cell_ids"] == baseline_top


def test_priority_scenario_reweights_and_normalises(client):
    # Distance-only scoring: ranking must follow nearest-facility distance.
    payload = client.get(
        "/api/priority-scenario?w_population=0&w_growth=0&w_distance=40&w_road=0&w_flood=0"
    ).get_json()
    assert payload["weights"] == {"population": 0.0, "growth": 0.0, "distance": 100.0, "road": 0.0, "flood": 0.0}
    dist_full = payload["scales"]["distance_full_m"]

    con = db()
    try:
        dist = {
            row["cell_id"]: min((row["dist_facility_m"] or dist_full) / dist_full, 1.0)
            for row in con.execute("SELECT cell_id, dist_facility_m FROM cells")
        }
    finally:
        con.close()
    top = payload["top_cell_ids"]
    assert all(dist[a] >= dist[b] for a, b in zip(top, top[1:]))


def test_baseline_priority_cells_are_populated_places_beyond_5_km():
    # Guards against ranking empty remote land (forest reserves, hills) first.
    con = db()
    try:
        rows = con.execute(
            """
            SELECT c.population, c.dist_facility_m
            FROM recommendations r JOIN cells c USING (cell_id)
            """
        ).fetchall()
        total = con.execute("SELECT SUM(population) FROM cells").fetchone()[0]
        district = con.execute(
            """SELECT population_est FROM population_summary
               WHERE area_name = 'Dedza District' AND coverage = 'total'"""
        ).fetchone()[0]
    finally:
        con.close()

    assert len(rows) == 12
    assert all(row["population"] >= 1000 for row in rows)
    assert all(row["dist_facility_m"] > 5000 for row in rows)
    # Cell totals use the same pixel rules as the district zonal summary.
    assert total == pytest.approx(district, rel=0.001)


def test_population_weight_ranks_most_populated_cells_first(client):
    payload = client.get(
        "/api/priority-scenario?w_population=100&w_growth=0&w_distance=0&w_road=0&w_flood=0&served_km=50"
    ).get_json()
    full = payload["scales"]["population_full"]
    con = db()
    try:
        population = {
            row["cell_id"]: min(row["population_2030"] / full, 1.0)
            for row in con.execute("SELECT cell_id, population_2030 FROM cells")
        }
    finally:
        con.close()
    top = payload["top_cell_ids"]
    assert all(population[a] >= population[b] for a, b in zip(top, top[1:]))
    assert all(population[cell_id] == 1.0 for cell_id in top)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("w_population=0&w_growth=0&w_distance=0&w_road=0&w_flood=0", "At least one"),
        ("w_population=101", "between 0 and 100"),
        ("w_distance=-5", "between 0 and 100"),
        ("w_road=abc", "must be numbers"),
        ("served_km=0", "served_km"),
    ],
)
def test_priority_scenario_rejects_invalid_input(client, query, message):
    response = client.get(f"/api/priority-scenario?{query}")
    assert response.status_code == 400
    assert message in response.get_json()["error"]


def test_motorcycle_speed_assumptions_follow_road_class():
    assert motorcycle_speed_kmh("primary") > motorcycle_speed_kmh("residential")
    assert motorcycle_speed_kmh("residential") > motorcycle_speed_kmh("track")


def test_route_estimate_returns_distance_time_and_alternative_choices(monkeypatch):
    start = (0.0, 0.0)
    slow_middle = (0.01, 0.0)
    fast_middle = (0.01, 0.01)
    target = (0.02, 0.0)
    graph = {node: [] for node in (start, slow_middle, fast_middle, target)}

    def connect(first, second, speed_kmh):
        distance = haversine_km(first, second)
        time = distance / speed_kmh * 60
        graph[first].append((second, distance, time))
        graph[second].append((first, distance, time))

    connect(start, slow_middle, 10)
    connect(slow_middle, target, 10)
    connect(start, fast_middle, 40)
    connect(fast_middle, target, 40)
    connect(start, target, 10)
    coordinates = {node: node for node in graph}
    projected = {node: project_road_node(node) for node in graph}
    monkeypatch.setattr(
        "sdss.routing.load_road_network",
        lambda *_args, **_kwargs: (graph, coordinates, projected),
    )

    result = calculate_road_route(start, target)

    assert result is not None
    alternatives = result["alternatives"]
    assert len(alternatives) == 3
    assert alternatives[0]["label"] == "Shortest distance"
    assert alternatives[1]["label"] == "Quickest estimated"
    assert (
        alternatives[1]["estimated_network_time_min"]
        < alternatives[0]["estimated_network_time_min"]
    )
    assert alternatives[2]["label"].startswith("Alternative")
    assert len({
        tuple(
            tuple(tuple(point) for point in feature["geometry"]["coordinates"])
            for feature in option["route"]["features"]
            if feature["properties"]["kind"] == "road"
        )
        for option in alternatives
    }) == 3


def test_road_time_search_honors_direction_and_time_limit():
    start = (34.0, -14.0)
    middle = (34.001, -14.0)
    target = (34.002, -14.0)
    graph = {
        start: [(middle, 0.1, 20.0)],
        middle: [(target, 0.1, 15.0)],
        target: [],
    }

    assert shortest_road_time_to_target(graph, start, target, 60) == pytest.approx(35)
    assert shortest_road_time_to_target(graph, start, target, 30) is None
    assert shortest_road_time_to_target(graph, target, start, 60) is None

    # The multi-source search used for scenarios gives the same directed times.
    times = routing.road_times_to_sources(graph, {target}, 60)
    assert times[start] == pytest.approx(35)
    assert times[middle] == pytest.approx(15)
    assert routing.road_times_to_sources(graph, {target}, 30).get(start) is None
    assert routing.road_times_to_sources(graph, {start}, 60).get(target) is None


def test_scenario_road_access_compares_candidate_with_existing_service(monkeypatch):
    start = (34.0, -14.0)
    candidate = (34.001, -14.0)
    existing = (34.002, -14.0)
    already_served = (34.003, -14.0)
    disconnected = (34.004, -14.0)
    graph = {
        start: [(candidate, 0.1, 20.0)],
        candidate: [],
        existing: [],
        already_served: [(candidate, 0.1, 45.0), (existing, 0.1, 15.0)],
        disconnected: [],
    }
    node_by_point = {point: point for point in graph}
    payloads = {
        "facilities": [{"lon": existing[0], "lat": existing[1]}],
        "schools": [],
        "settlements": [
            {"name": "New access", "adm3_name": "TA A", "lon": start[0], "lat": start[1]},
            {"name": "Already served", "adm3_name": "TA B", "lon": already_served[0], "lat": already_served[1]},
            {"name": "Disconnected", "adm3_name": "TA C", "lon": disconnected[0], "lat": disconnected[1]},
        ],
    }
    projected = {node: project_road_node(node) for node in graph}
    monkeypatch.setattr("sdss.scenario.load_payloads", lambda: payloads)
    monkeypatch.setattr("sdss.scenario.load_road_network", lambda *_args, **_kwargs: (graph, {node: node for node in graph}, projected))
    monkeypatch.setattr(
        "sdss.scenario.nearest_road_node",
        lambda point, _projected: node_by_point[point],
    )
    scenario_baseline_road_times.cache_clear()
    try:
        result = estimate_scenario_road_access(candidate[0], candidate[1], "health")
    finally:
        scenario_baseline_road_times.cache_clear()

    by_name = {settlement["name"]: settlement for settlement in result["settlements"]}
    assert result["available"] is True
    assert result["time_bands_minutes"] == [30, 60]
    assert by_name["New access"]["candidate_time_min"] == pytest.approx(20)
    assert by_name["New access"]["existing_time_min"] is None
    assert by_name["Already served"]["candidate_time_min"] == pytest.approx(45)
    assert by_name["Already served"]["existing_time_min"] == pytest.approx(15)
    assert by_name["Disconnected"]["candidate_time_min"] is None


def test_route_endpoint_requires_coordinate_pairs(client):
    response = client.post("/api/route", json={})
    assert response.status_code == 400
    assert "coordinates" in response.get_json()["error"]


def test_route_endpoint_returns_mapped_road_route(client):
    payloads = load_payloads()
    settlement = next(
        row for row in payloads["settlements"] if row["name"] == "Abrahamu"
    )
    facility = next(
        row for row in payloads["facilities"] if row["name"] == "Bembeke Health Centre"
    )
    response = client.post(
        "/api/route",
        json={
            "origin": [settlement["lon"], settlement["lat"]],
            "destination": [facility["lon"], facility["lat"]],
        },
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result["route"]["type"] == "FeatureCollection"
    assert result["alternatives"]
    assert result["alternatives"][0]["route"] == result["route"]
    assert result["network_distance_km"] > 0
    assert result["total_distance_km"] >= result["network_distance_km"]
    assert result["estimated_network_time_min"] > 0
    assert any(feature["properties"]["kind"] == "road" for feature in result["route"]["features"])


@pytest.mark.parametrize("facility_type", ["health", "school"])
def test_scenario_population_endpoint_estimates_additional_population_by_ta(client, facility_type):
    # A populated cell more than 5 km from every existing service of this type.
    payloads = load_payloads()
    services = payloads["schools"] if facility_type == "school" else payloads["facilities"]
    con = db()
    try:
        cells = con.execute("SELECT lon, lat FROM cells WHERE population > 1000").fetchall()
    finally:
        con.close()
    site = max(
        cells,
        key=lambda cell: min(
            haversine_km((cell["lon"], cell["lat"]), (service["lon"], service["lat"]))
            for service in services
        ),
    )

    response = client.post(
        "/api/scenario-population",
        json={
            "coordinates": [site["lon"], site["lat"]],
            "type": facility_type,
            "radius_km": 5,
        },
    )

    assert response.status_code == 200
    result = response.get_json()
    by_ta = result["by_traditional_authority"]
    assert result["additional_population_estimate"] > 0
    assert result["population_within_radius"] >= result["already_within_existing_service_radius"]
    assert result["additional_population_estimate"] + result["already_within_existing_service_radius"] == pytest.approx(
        result["population_within_radius"], abs=0.1
    )
    assert sum(area["population_estimate"] for area in by_ta) == pytest.approx(
        result["additional_population_estimate"], abs=0.1
    )
    assert result["method"].startswith("WorldPop 2025 100 m population pixels")
    assert result["road_time_access"]["time_bands_minutes"] == [30, 60]
    assert result["road_time_access"]["capacity_assessment_available"] is False


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"coordinates": [34.3, -14.3], "type": "health", "radius_km": 0}, "radius_km"),
        ({"coordinates": [0, 0], "type": "health", "radius_km": 5}, "inside Dedza"),
        ({"coordinates": [34.3, -14.3], "type": "other", "radius_km": 5}, "type must"),
    ],
)
def test_scenario_population_endpoint_rejects_invalid_candidate(payload, message, client):
    response = client.post("/api/scenario-population", json=payload)

    assert response.status_code == 400
    assert message in response.get_json()["error"]


def _unserved_cell_point():
    con = db()
    try:
        site = con.execute(
            "SELECT lon, lat FROM cells WHERE coverage = 'unserved' LIMIT 1"
        ).fetchone()
        return site["lon"], site["lat"]
    finally:
        con.close()


def _write_site_constraints(path, point):
    lon, lat = point
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [lon - 0.001, lat - 0.001],
            [lon + 0.001, lat - 0.001],
            [lon + 0.001, lat + 0.001],
            [lon - 0.001, lat + 0.001],
            [lon - 0.001, lat - 0.001],
        ]],
    }
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE buildings (
                id INTEGER PRIMARY KEY,
                min_lon REAL,
                min_lat REAL,
                max_lon REAL,
                max_lat REAL,
                height REAL,
                confidence REAL,
                geometry TEXT
            );
            """
        )
        con.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("buildings_source", "test"),
                ("buildings_date", "2026-01-01"),
                ("buildings_license", "test"),
            ],
        )
        con.execute(
            """
            INSERT INTO buildings (
                min_lon, min_lat, max_lon, max_lat, height, confidence, geometry
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lon - 0.001,
                lat - 0.001,
                lon + 0.001,
                lat + 0.001,
                -1,
                0.9,
                json.dumps(geometry),
            ),
        )
        con.commit()
    finally:
        con.close()


def test_site_screening_blocks_candidate_inside_detected_building(client, tmp_path, monkeypatch):
    point = _unserved_cell_point()
    constraints_path = tmp_path / "site_constraints.sqlite"
    _write_site_constraints(constraints_path, point)
    monkeypatch.setattr(config, "SITE_CONSTRAINT_DB_PATH", constraints_path)
    assert building_at(*point) is not None

    response = client.post("/api/site-screening", json={"coordinates": list(point)})

    assert response.status_code == 200
    result = response.get_json()
    assert result["buildings_available"] is True
    assert result["occupied"] is True, result
    assert set(result) == {
        "buildings_available", "occupied", "building", "building_message"
    }


def test_site_screening_reports_unavailable_buildings_instead_of_clear_site(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "SITE_CONSTRAINT_DB_PATH", tmp_path / "not-imported.sqlite")

    response = client.post(
        "/api/site-screening",
        json={"coordinates": list(_unserved_cell_point())},
    )

    assert response.status_code == 200
    result = response.get_json()
    assert result["buildings_available"] is False
    assert result["occupied"] is False
    assert "not imported" in result["building_message"]
    assert set(result) == {
        "buildings_available", "occupied", "building", "building_message"
    }


def test_buildings_endpoint_validates_bbox(client):
    response = client.get("/api/buildings?bbox=0,0,1")

    assert response.status_code == 400
    assert "bbox" in response.get_json()["error"]


def test_buildings_endpoint_returns_features_in_view(client, tmp_path, monkeypatch):
    point = _unserved_cell_point()
    constraints_path = tmp_path / "site_constraints.sqlite"
    _write_site_constraints(constraints_path, point)
    monkeypatch.setattr(config, "SITE_CONSTRAINT_DB_PATH", constraints_path)
    lon, lat = point

    response = client.get(
        f"/api/buildings?bbox={lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}"
    )

    assert response.status_code == 200
    result = response.get_json()
    assert result["available"] is True
    assert len(result["features"]) == 1
    assert result["features"][0]["properties"]["confidence"] == pytest.approx(0.9)


def test_point_in_geometry_respects_polygon_holes():
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
            [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]],
        ],
    }

    assert point_in_geometry((0.5, 0.5), geometry)
    assert not point_in_geometry((2, 2), geometry)
    assert point_in_geometry((1, 2), geometry)


def test_cell_planning_indicators_are_calculated_from_the_rasters():
    con = db()
    try:
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        totals = con.execute(
            "SELECT SUM(population_2020), SUM(population), SUM(population_2030) FROM cells"
        ).fetchone()
        bad_shares = con.execute(
            """SELECT COUNT(*) FROM cells
               WHERE road_access_share NOT BETWEEN 0 AND 1
                  OR flood_area_share NOT BETWEEN 0 AND 1
                  OR flood_pop_share NOT BETWEEN 0 AND 1"""
        ).fetchone()[0]
        growth_mismatch = con.execute(
            "SELECT COUNT(*) FROM cells WHERE ABS(growth - (population_2030 - population)) > 0.01"
        ).fetchone()[0]
        flooded_cells = con.execute("SELECT COUNT(*) FROM cells WHERE flood_area_share > 0").fetchone()[0]
        travel = con.execute(
            "SELECT COUNT(*), SUM(travel_min IS NULL), MIN(travel_min) FROM cells WHERE population > 100"
        ).fetchone()
    finally:
        con.close()

    for year, total in zip((2020, 2025, 2030), totals):
        assert total == pytest.approx(float(meta[f"population_{year}"]), rel=1e-6)
    assert totals[0] < totals[1] < totals[2]  # Dedza's population is growing
    assert bad_shares == 0
    assert growth_mismatch == 0
    assert flooded_cells > 0
    assert travel[1] <= travel[0] * 0.05  # almost every populated cell has a road route
    assert travel[2] >= 0


def test_recommendation_actions_come_from_cell_indicators():
    con = db()
    try:
        rows = con.execute(
            """SELECT r.action, r.growth, c.flood_area_share
               FROM recommendations r JOIN cells c USING (cell_id)"""
        ).fetchall()
    finally:
        con.close()
    for row in rows:
        if row["growth"] >= 1:
            assert f"{round(row['growth']):,} more residents by 2030" in row["action"]
        if row["flood_area_share"] >= 0.05:
            assert "1-in-100-year flood zone" in row["action"]
        else:
            assert "flood zone" not in row["action"]


def _flooded_point():
    import rasterio as rio

    with rio.open(config.FLOOD_DEPTH_PATH) as source:
        depth = source.read(1)
        rows, cols = np.nonzero((depth > 1) & (depth != source.nodata))
        boundary = load_payloads()["boundary"]["features"][0]["geometry"]
        for row, col in zip(rows, cols):
            lon, lat = source.xy(row, col)
            if point_in_geometry((lon, lat), boundary):
                return lon, lat
    raise AssertionError("no flooded pixel inside Dedza")


def test_site_check_fails_inside_the_flood_zone(client):
    lon, lat = _flooded_point()
    result = client.get(f"/api/site-check?lon={lon}&lat={lat}").get_json()
    flood = next(check for check in result["checks"] if check["key"] == "flood")
    assert flood["status"] == "fail"
    assert flood["value"] > 1
    assert result["suitable"] is False
    assert result["rules"]["road_access_km"] == pytest.approx(
        float(load_payloads()["meta"]["road_access_km"])
    )


def test_site_check_reports_distance_to_nearest_classified_road(client):
    response = client.get("/api/site-check?lon=34.3242&lat=-14.3778")
    assert response.status_code == 200
    road = next(check for check in response.get_json()["checks"] if check["key"] == "road")
    assert road["status"] in {"pass", "warn", "fail"}
    assert road["value"] >= 0
    assert client.get("/api/site-check?lon=abc&lat=1").status_code == 400


def test_scenario_population_reports_2030_reach_and_site_checks(client):
    con = db()
    try:
        site = con.execute(
            "SELECT lon, lat FROM cells WHERE coverage = 'unserved' ORDER BY population DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    result = client.post(
        "/api/scenario-population",
        json={"coordinates": [site["lon"], site["lat"]], "type": "health", "radius_km": 5},
    ).get_json()
    assert result["additional_population_2030"] > result["additional_population_estimate"] > 0
    assert result["population_within_radius_2030"] >= result["additional_population_2030"]
    assert {check["key"] for check in result["site_suitability"]["checks"]} == {
        "flood", "road", "slope", "land_cover",
    }


def test_settlements_carry_2030_population_and_site_suitability(client):
    features = client.get("/api/settlements").get_json()["features"]
    assert features
    props = [feature["properties"] for feature in features]
    assert all("site_suitable" in p and "population_2030" in p for p in props)
    assert any(p["site_suitable"] is False for p in props)
    assert all(not p["site_suitable"] or not any(
        issue.startswith("Flood risk: Inside") for issue in p["site_issues"]
    ) for p in props)
    grown = [p for p in props if (p["population"] or 0) > 100]
    assert all(p["population_2030"] >= p["population"] for p in grown)


def test_flood_tiles_render(client):
    response = client.get("/api/environmental-tiles/flood/9/304/277.png")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_cell_distance_is_walking_distance_and_never_shorter_than_straight_line():
    con = db()
    try:
        rows = con.execute("SELECT dist_facility_m, straight_dist_m FROM cells").fetchall()
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    assert all(row["dist_facility_m"] >= row["straight_dist_m"] - 1 for row in rows)
    longer = sum(row["dist_facility_m"] > row["straight_dist_m"] * 1.05 for row in rows)
    assert longer > len(rows) / 2  # paths wind, so most cells are noticeably farther on foot
    assert meta["distance_method"].startswith("walking network distance")


def test_road_network_modes_and_shortest_costs():
    from scripts.road_network import NodeIndex, build_graph, shortest_costs

    line = lambda *coords: json.dumps({"type": "LineString", "coordinates": [list(c) for c in coords]})
    rows = [
        ("primary", "", line((34.0, -14.0), (34.01, -14.0))),
        ("path", "", line((34.01, -14.0), (34.02, -14.0))),
        ("residential", "yes", line((34.02, -14.0), (34.03, -14.0))),
    ]
    walking = build_graph(rows, "walking")
    vehicle = build_graph(rows, "vehicle")
    assert (34.02, -14.0) in walking and (34.015, -14.0) not in vehicle
    assert all(edge[0] != (34.02, -14.0) for edge in vehicle.get((34.01, -14.0), []))  # no path for vehicles
    assert vehicle[(34.03, -14.0)] == []  # one-way street
    assert any(edge[0] == (34.02, -14.0) for edge in walking[(34.03, -14.0)])  # walking ignores one-way

    costs, origin = shortest_costs(walking, {(34.0, -14.0): 0.5}, labels={(34.0, -14.0): 7})
    assert costs[(34.03, -14.0)] == pytest.approx(0.5 + 3 * haversine_km((34.0, -14.0), (34.01, -14.0)), rel=1e-3)
    assert origin[(34.03, -14.0)] == 7
    within, _ = shortest_costs(walking, {(34.0, -14.0): 0.0}, limit=1.5)
    assert (34.01, -14.0) in within and (34.03, -14.0) not in within

    index = NodeIndex(walking)
    nearest, distance = index.nearest_points(np.array([34.011, 34.029]), np.array([-14.0, -14.0]))
    assert [index.nodes[i] for i in nearest] == [(34.01, -14.0), (34.03, -14.0)]
    assert distance[0] == pytest.approx(0.108, abs=0.002)

    # A capped search leaves points with no node within max_km unmatched.
    far, far_distance = index.nearest_points(np.array([34.011, 34.2]), np.array([-14.0, -14.0]), max_km=1.0)
    assert index.nodes[far[0]] == (34.01, -14.0) and far[1] == -1 and np.isinf(far_distance[1])


def test_candidate_walking_reach_area_matches_population_rule(client):
    from shapely.geometry import Point, shape

    lon, lat = 34.33, -14.38
    estimate = client.post(
        "/api/scenario-population", json={"coordinates": [lon, lat], "type": "health", "radius_km": 5}
    ).get_json()
    area = shape(estimate["walking_reach_geojson"])
    assert area.is_valid and area.contains(Point(lon, lat))
    # Walking reach is never larger than the straight-line circle of the same radius.
    area_km2 = area.area * 110.574 * 111.320 * np.cos(np.radians(lat))
    assert 0 < area_km2 < np.pi * 5 ** 2
    # Every reached settlement lies in (or on the sampled edge of) the drawn area.
    settlements = load_payloads()["settlements"]
    for row in estimate["reached_settlements"]:
        point = Point(float(settlements[row["index"]]["lon"]), float(settlements[row["index"]]["lat"]))
        assert area.buffer(0.003).contains(point)


def test_health_walking_area_matches_served_cells(client):
    from shapely.geometry import shape

    response = client.get("/api/health-walking-area")
    assert response.status_code == 200
    feature = response.get_json()
    area = shape(feature["geometry"])
    assert feature["properties"]["limit_km"] == 5.0 and area.is_valid
    area_km2 = area.area * 110.574 * 111.320 * np.cos(np.radians(-14.3))
    con = db()
    try:
        served_km2, district_km2 = con.execute(
            "SELECT SUM(CASE WHEN coverage = 'served' THEN area_km2 ELSE 0 END), SUM(area_km2) FROM cells"
        ).fetchone()
    finally:
        con.close()
    # Measured from cell population centres vs. every 250 m location: close, not identical.
    assert area_km2 < district_km2
    assert abs(area_km2 - served_km2) / district_km2 < 0.1


def test_walking_route_uses_paths_at_walking_speed(client):
    payloads = load_payloads()
    settlement = next(row for row in payloads["settlements"] if row["name"] == "Abrahamu")
    facility = next(row for row in payloads["facilities"] if row["name"] == "Bembeke Health Centre")
    body = {"origin": [settlement["lon"], settlement["lat"]], "destination": [facility["lon"], facility["lat"]]}
    walking = client.post("/api/route", json={**body, "mode": "walking"}).get_json()
    vehicle = client.post("/api/route", json={**body, "mode": "vehicle"}).get_json()
    assert walking["mode"] == "walking" and vehicle["mode"] == "vehicle"
    assert walking["estimated_network_time_min"] == pytest.approx(walking["network_distance_km"] / 5 * 60, rel=1e-6)
    assert walking["network_distance_km"] <= vehicle["network_distance_km"] + 1e-6  # footpaths can only shorten it
    assert walking["estimated_network_time_min"] > vehicle["estimated_network_time_min"]
    assert client.post("/api/route", json={**body, "mode": "bicycle"}).status_code == 400


def test_settlement_walking_distance_to_nearest_service(client):
    features = client.get("/api/settlements").get_json()["features"]
    health_sites = walking.service_sites("health")
    schools = load_payloads()["schools"]
    checked = 0
    for feature in features[:200]:
        p = feature["properties"]
        if p["walk_km_health"] is None:
            continue
        point = tuple(feature["geometry"]["coordinates"])
        straight = min(haversine_km(point, (s["lon"], s["lat"])) for s in health_sites)
        assert p["walk_km_health"] >= straight - 0.05
        school = schools[p["nearest_school_index"]]
        assert p["walk_km_school"] >= haversine_km(point, (school["lon"], school["lat"])) - 0.05
        checked += 1
    assert checked > 150


def test_school_level_from_name():
    assert school_level("Bembeke CDSS") == "Secondary"
    assert school_level("Chawa Open") == "Secondary"
    assert school_level("St. Kizito Seminary") == "Secondary"
    assert school_level("Kanyama J.P") == "Junior primary"
    assert school_level("Kanyama F.P") == "Primary"
    assert school_level("Bembeke RC") == "School"
    assert level_group("Kanyama J.P") == "primary"
    assert level_group("Bembeke RC") is None


def test_settlement_walking_distance_by_school_level(client):
    features = client.get("/api/settlements").get_json()["features"]
    schools = client.get("/api/schools").get_json()["features"]
    within = {level: 0 for level in ("", "_primary", "_secondary")}
    for feature in features:
        p = feature["properties"]
        for level in ("_primary", "_secondary"):
            index = p[f"nearest_school{level}_index"]
            if index is None:
                continue
            assert schools[index]["properties"]["level_group"] == level[1:]
            # The nearest school of any level is never further than one of a given level.
            assert p["walk_km_school"] <= p[f"walk_km_school{level}"] + 1e-6
        for level in within:
            if p[f"walk_km_school{level}"] is not None and p[f"walk_km_school{level}"] <= 5:
                within[level] += 1
    # Secondary schools are far scarcer, so far fewer settlements are within a 5 km walk.
    assert within[""] >= within["_primary"] > within["_secondary"]


def test_suggest_secondary_school_sites(client):
    body = {"type": "school", "radius_km": 5, "school_level": "secondary"}
    picks = client.post("/api/suggest-sites", json=body).get_json()["picks"]
    assert picks and picks[0]["people"] > 0
    features = client.get("/api/settlements").get_json()["features"]
    for pick in picks:
        walk = features[pick["index"]]["properties"]["walk_km_school_secondary"]
        assert walk is None or walk > 0
    estimate = client.post(
        "/api/scenario-population",
        json={"coordinates": picks[0]["coordinates"], "type": "school", "radius_km": 5, "school_level": "secondary"},
    ).get_json()
    assert estimate["additional_population_estimate"] > 0
    assert "secondary school" in estimate["method"]
    assert client.post("/api/suggest-sites", json={**body, "school_level": "tertiary"}).status_code == 400
    assert client.post(
        "/api/scenario-population",
        json={"coordinates": picks[0]["coordinates"], "type": "school", "radius_km": 5, "school_level": "x"},
    ).status_code == 400


def test_suggest_sites_maximises_walking_reach(client):
    response = client.post("/api/suggest-sites", json={"type": "health", "radius_km": 5})
    assert response.status_code == 200
    picks = response.get_json()["picks"]
    assert 1 <= len(picks) <= 3
    gains = [pick["people"] for pick in picks]
    assert gains == sorted(gains, reverse=True) and gains[0] > 0
    planning = {i: row for i, row in enumerate(client.get("/api/settlements").get_json()["features"])}
    for pick in picks:
        assert planning[pick["index"]]["properties"]["site_suitable"] is True
    # The top pick's reach matches an independent walking estimate from that site.
    top = picks[0]
    estimate = client.post(
        "/api/scenario-population", json={"coordinates": top["coordinates"], "type": "health", "radius_km": 5}
    ).get_json()
    assert len(estimate["reached_settlements"]) >= top["settlement_count"]
    assert all(row["walk_km"] <= 5 for row in estimate["reached_settlements"])

    excluded = client.post(
        "/api/suggest-sites", json={"type": "health", "radius_km": 5, "exclude": [top["coordinates"]]}
    ).get_json()["picks"]
    assert top["index"] not in [pick["index"] for pick in excluded]
    assert client.post("/api/suggest-sites", json={"type": "health", "radius_km": 5, "area": "Nowhere"}).status_code == 400
    assert client.post("/api/suggest-sites", json={"type": "clinic", "radius_km": 5}).status_code == 400
