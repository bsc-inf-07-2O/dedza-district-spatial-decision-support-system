"""Physical suitability checks for a proposed facility site.

Each check samples real data at the site:
  * flood      - JRC GloFAS 1-in-100-year flood depth and permanent water
  * road       - distance to the nearest classified road (OSM trunk to tertiary)
  * slope      - terrain slope from the Mapzen Terrain Tiles DEM
  * land cover - ESA WorldCover 2021 class

The thresholds below are planning assumptions, kept in one place and returned
with every result so the reasoning is visible.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio.transform import rowcol
from shapely import STRtree
from shapely.geometry import Point, shape

SITE_RULES = {
    "flood_buffer_m": 200.0,   # flooding this close to the site is a warning
    "road_warn_km": 5.0,       # beyond road_access_km: warn; beyond this: fail
    "slope_pass_deg": 5.0,     # gentle
    "slope_fail_deg": 15.0,    # steep: costly earthworks, poor access
}
WORLDCOVER_CLASSES = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / sparse vegetation",
    70: "Snow and ice",
    80: "Permanent water",
    90: "Herbaceous wetland",
    95: "Mangroves",
    100: "Moss and lichen",
}
UNBUILDABLE_COVER = {80, 90, 95}
REVIEW_COVER = {10}  # tree cover may be a forest reserve
CHECK_LABELS = {
    "flood": "Flood risk",
    "road": "Road access",
    "slope": "Terrain slope",
    "land_cover": "Land cover",
}

_LAT0 = math.radians(-14.2)  # Dedza; local equirectangular projection in km


def _to_km(coords: np.ndarray) -> np.ndarray:
    return coords * np.array([111.320 * math.cos(_LAT0), 110.574])


def _mercator(lon: float, lat: float) -> tuple[float, float]:
    radius = 6_378_137.0
    return (
        radius * math.radians(lon),
        radius * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)),
    )


class SiteChecker:
    def __init__(
        self,
        roads: list[str],
        road_access_km: float,
        flood_path: Path,
        water_path: Path,
        slope_path: Path,
        land_cover_path: Path,
    ) -> None:
        self.road_access_km = road_access_km
        lines = [shapely.transform(shape(json.loads(geojson)), _to_km) for geojson in roads]
        self.roads = [line for line in lines if not line.is_empty]
        self.road_tree = STRtree(self.roads) if self.roads else None
        with rasterio.open(flood_path) as src:
            self.flood = np.where(src.read(1) == src.nodata, 0, src.read(1)).astype(np.float32)
            self.flood_transform = src.transform
        with rasterio.open(water_path) as src:
            water = src.read(1)
            self.water = (water == 1)
            self.water_transform = src.transform
        self.slope_path = slope_path if slope_path.is_file() else None
        self.land_cover_path = land_cover_path if land_cover_path.is_file() else None

    @staticmethod
    def _pixel(transform, lon: float, lat: float, shape_: tuple[int, int]) -> tuple[int, int] | None:
        row, col = rowcol(transform, lon, lat)
        row, col = int(row), int(col)
        if 0 <= row < shape_[0] and 0 <= col < shape_[1]:
            return row, col
        return None

    def _flood(self, lon: float, lat: float) -> dict:
        cell = self._pixel(self.flood_transform, lon, lat, self.flood.shape)
        water_cell = self._pixel(self.water_transform, lon, lat, self.water.shape)
        depth = float(self.flood[cell]) if cell else 0.0
        in_water = bool(self.water[water_cell]) if water_cell else False
        pixel_m = abs(self.flood_transform.a) * 111_320 * math.cos(_LAT0)
        reach = max(1, math.ceil(SITE_RULES["flood_buffer_m"] / pixel_m))
        nearby = 0.0
        if cell:
            r, c = cell
            window = self.flood[max(0, r - reach): r + reach + 1, max(0, c - reach): c + reach + 1]
            nearby = float(window.max()) if window.size else 0.0
        if in_water:
            return self._result("flood", "fail", None, "Site is on permanent water.")
        if depth > 0:
            return self._result(
                "flood", "fail", round(depth, 2),
                f"Inside the 1-in-100-year flood zone ({depth:.1f} m modelled depth).",
            )
        if nearby > 0:
            return self._result(
                "flood", "warn", 0.0,
                f"Outside the flood zone, but flooding is modelled within {SITE_RULES['flood_buffer_m']:.0f} m.",
            )
        return self._result("flood", "pass", 0.0, "Outside the 1-in-100-year flood zone.")

    def _road(self, lon: float, lat: float) -> dict:
        if self.road_tree is None:
            return self._result("road", "unknown", None, "No classified roads are mapped.")
        point = Point(*_to_km(np.array([lon, lat], dtype=float)))
        _, distance = self.road_tree.query_nearest(point, return_distance=True)
        km = float(distance[0])
        if km <= self.road_access_km:
            status = "pass"
        elif km <= SITE_RULES["road_warn_km"]:
            status = "warn"
        else:
            status = "fail"
        return self._result("road", status, round(km, 2), f"{km:.1f} km to the nearest classified road.")

    def _slope(self, lon: float, lat: float, dataset) -> dict:
        if dataset is None:
            return self._result("slope", "unknown", None, "Slope layer is not installed.")
        value = next(dataset.sample([_mercator(lon, lat)]))[0]
        if dataset.nodata is not None and value == dataset.nodata or not np.isfinite(value):
            return self._result("slope", "unknown", None, "No slope data at this point.")
        degrees = float(value)
        if degrees < SITE_RULES["slope_pass_deg"]:
            status = "pass"
        elif degrees < SITE_RULES["slope_fail_deg"]:
            status = "warn"
        else:
            status = "fail"
        return self._result("slope", status, round(degrees, 1), f"{degrees:.1f}° slope.")

    def _land_cover(self, lon: float, lat: float, dataset) -> dict:
        if dataset is None:
            return self._result("land_cover", "unknown", None, "Land-cover layer is not installed.")
        value = int(next(dataset.sample([(lon, lat)]))[0])
        name = WORLDCOVER_CLASSES.get(value)
        if name is None:
            return self._result("land_cover", "unknown", None, "No land-cover data at this point.")
        if value in UNBUILDABLE_COVER:
            return self._result("land_cover", "fail", name, f"{name}: not buildable.")
        if value in REVIEW_COVER:
            return self._result("land_cover", "warn", name, f"{name}: check it is not a forest reserve.")
        return self._result("land_cover", "pass", name, f"{name}.")

    @staticmethod
    def _result(key: str, status: str, value, detail: str) -> dict:
        return {"key": key, "label": CHECK_LABELS[key], "status": status, "value": value, "detail": detail}

    def check_many(self, points: list[tuple[float, float]]) -> list[dict]:
        slope = rasterio.open(self.slope_path) if self.slope_path else None
        cover = rasterio.open(self.land_cover_path) if self.land_cover_path else None
        try:
            results = []
            for lon, lat in points:
                checks = [
                    self._flood(lon, lat),
                    self._road(lon, lat),
                    self._slope(lon, lat, slope),
                    self._land_cover(lon, lat, cover),
                ]
                results.append({
                    "suitable": not any(check["status"] == "fail" for check in checks),
                    "checks": checks,
                })
            return results
        finally:
            if slope:
                slope.close()
            if cover:
                cover.close()

    def check(self, lon: float, lat: float) -> dict:
        result = self.check_many([(lon, lat)])[0]
        result["rules"] = {**SITE_RULES, "road_access_km": self.road_access_km}
        return result
