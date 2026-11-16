import pytest

from app import (
    app,
    calculate_road_route,
    db,
    estimate_scenario_road_access,
    haversine_km,
    load_payloads,
    motorcycle_speed_kmh,
    project_road_node,
    scenario_baseline_road_times,
    shortest_road_time_to_target,
)


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


def test_summary_endpoint_returns_metrics(client):
    response = client.get("/api/summary")
    assert response.status_code == 200
    payload = response.get_json()
    assert "meta" in payload
    assert "type_counts" in payload
    assert "coverage" in payload
    assert payload["coverage"]


def test_access_summary_endpoint(client):
    response = client.get("/api/access-summary")
    assert response.status_code == 200
    payload = response.get_json()
    assert "avg_access_score" in payload
    assert "cells" in payload
    assert payload["cells"]
    assert 0 <= payload["avg_access_score"] <= 100


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
        "app.load_road_network",
        lambda: (graph, coordinates, projected),
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
    monkeypatch.setattr("app.load_payloads", lambda: payloads)
    monkeypatch.setattr("app.load_road_network", lambda: (graph, {node: node for node in graph}, projected))
    monkeypatch.setattr(
        "app.nearest_road_node",
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
    con = db()
    try:
        site = con.execute(
            "SELECT lon, lat FROM cells WHERE coverage = 'unserved' ORDER BY priority_score DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()

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
