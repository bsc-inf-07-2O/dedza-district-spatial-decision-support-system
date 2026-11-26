"""PNG map tiles (Web Mercator, 256 px) for the environmental rasters."""

from __future__ import annotations

import math
import warnings

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject

TILE_SIZE = 256

# ESA WorldCover class -> colour.
LAND_COVER_COLORS = {
    10: (38, 115, 45),
    20: (153, 191, 74),
    30: (192, 220, 130),
    40: (232, 198, 83),
    50: (202, 75, 64),
    60: (184, 164, 133),
    70: (235, 239, 240),
    80: (61, 144, 201),
    90: (76, 174, 143),
    95: (37, 112, 95),
    100: (123, 167, 91),
}
# Depth bands of the GloFAS categorised map: <1, 1-3, 3-10, >10 m (lower exclusive, upper inclusive).
FLOOD_DEPTH_COLORS = (
    (0, 1, (158, 202, 225)),
    (1, 3, (66, 146, 198)),
    (3, 10, (33, 102, 172)),
    (10, math.inf, (8, 48, 107)),
)
# Slope in degrees (lower inclusive, upper exclusive).
SLOPE_COLORS = (
    (0, 5, (65, 150, 88)),
    (5, 15, (207, 197, 93)),
    (15, 30, (221, 130, 65)),
    (30, math.inf, (171, 62, 56)),
)


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """West, south, east, north of an XYZ tile in EPSG:3857 metres."""
    tile_count = 1 << z
    world = 2 * math.pi * 6_378_137.0
    west = x / tile_count * world - world / 2
    east = (x + 1) / tile_count * world - world / 2
    north = world / 2 - y / tile_count * world
    south = world / 2 - (y + 1) / tile_count * world
    return west, south, east, north


def _read_tile_pixels(source_path, z: int, x: int, y: int) -> tuple[np.ndarray, float]:
    tile_transform = from_bounds(*_tile_bounds(z, x, y), TILE_SIZE, TILE_SIZE)
    with rasterio.open(source_path) as source:
        nodata = source.nodata if source.nodata is not None else 0
        pixels = np.full((TILE_SIZE, TILE_SIZE), nodata, dtype=np.float32)
        reproject(
            source=rasterio.band(source, 1),
            destination=pixels,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=tile_transform,
            dst_crs="EPSG:3857",
            dst_nodata=nodata,
            resampling=Resampling.nearest,
        )
    return pixels, nodata


def _colorize(layer: str, pixels: np.ndarray, nodata: float) -> np.ndarray:
    rgba = np.zeros((4, TILE_SIZE, TILE_SIZE), dtype=np.uint8)
    valid = np.isfinite(pixels) & (pixels != nodata)

    def paint(selected: np.ndarray, color: tuple[int, int, int], alpha: int) -> None:
        rgba[:3, selected] = np.asarray(color, dtype=np.uint8)[:, None]
        rgba[3, selected] = alpha

    if layer == "land_cover":
        for value, color in LAND_COVER_COLORS.items():
            paint(valid & (pixels == value), color, 190)
    elif layer == "flood":
        for lower, upper, color in FLOOD_DEPTH_COLORS:
            paint(valid & (pixels > lower) & (pixels <= upper), color, 200)
    else:
        for lower, upper, color in SLOPE_COLORS:
            paint(valid & (pixels >= lower) & (pixels < upper), color, 190)
    return rgba


def _encode_png(rgba: np.ndarray) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with MemoryFile() as memory_file:
            with memory_file.open(
                driver="PNG", width=TILE_SIZE, height=TILE_SIZE, count=4, dtype="uint8"
            ) as image:
                image.write(rgba)
            return memory_file.read()


def render_tile(layer: str, source_path, z: int, x: int, y: int) -> bytes:
    """PNG bytes of tile z/x/y for `layer` ("land_cover", "slope" or "flood")."""
    pixels, nodata = _read_tile_pixels(source_path, z, x, y)
    return _encode_png(_colorize(layer, pixels, nodata))
