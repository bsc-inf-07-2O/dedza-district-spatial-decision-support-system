"""Small GeoJSON geometry helpers shared by planning-layer import and screening."""

from __future__ import annotations

from collections.abc import Iterator


def iter_coordinates(value: object) -> Iterator[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        return
    if (
        len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        yield float(value[0]), float(value[1])
        return
    for item in value:
        yield from iter_coordinates(item)


def geometry_bounds(geometry: dict) -> tuple[float, float, float, float]:
    points = list(iter_coordinates(geometry.get("coordinates")))
    if not points:
        raise ValueError("Geometry has no coordinates.")
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _ring_location(point: tuple[float, float], ring: list) -> int:
    x, y = point
    inside = False
    for first, second in zip(ring, ring[1:]):
        x1, y1 = first[:2]
        x2, y2 = second[:2]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if (
            abs(cross) <= 1e-12
            and min(x1, x2) - 1e-12 <= x <= max(x1, x2) + 1e-12
            and min(y1, y2) - 1e-12 <= y <= max(y1, y2) + 1e-12
        ):
            return 0
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing_x:
                inside = not inside
    return 1 if inside else -1


def _point_in_polygon(point: tuple[float, float], rings: list) -> bool:
    if not rings or _ring_location(point, rings[0]) < 0:
        return False
    return not any(_ring_location(point, hole) == 1 for hole in rings[1:])


def point_in_geometry(point: tuple[float, float], geometry: dict) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        return _point_in_polygon(point, coordinates)
    if geometry_type == "MultiPolygon":
        return any(_point_in_polygon(point, polygon) for polygon in coordinates)
    if geometry_type == "GeometryCollection":
        return any(
            point_in_geometry(point, part)
            for part in geometry.get("geometries", [])
        )
    return False
