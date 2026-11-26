"""Small coordinate helpers with no data dependencies."""

from __future__ import annotations

import math


def valid_lon_lat(value: object) -> tuple[float, float] | None:
    """`value` as a (lon, lat) pair, or None if it is not a valid coordinate."""
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
    ):
        return None
    try:
        lon, lat = float(value[0]), float(value[1])
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lon, lat
