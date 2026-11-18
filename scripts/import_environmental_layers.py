"""Download and clip public land-cover and terrain rasters for Dedza."""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import warnings

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.mask import mask
from rasterio.transform import Affine
from rasterio.warp import transform_geom

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = ROOT / "data" / "raw" / "dedza_boundary.geojson"
OUTPUT_DIR = ROOT / "data" / "processed" / "environmental"
WORLD_COVER_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_S15E033_Map.tif"
)
TERRARIUM_TILE_URL = (
    "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"
)
TERRAIN_ZOOM = 12
MERCATOR_RADIUS = 6_378_137.0
MERCATOR_WORLD = 2 * math.pi * MERCATOR_RADIUS
DEM_NODATA = -32768.0
SLOPE_NODATA = -9999.0


def _coordinates(value: object):
    if not isinstance(value, list):
        return
    if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
        yield float(value[0]), float(value[1])
        return
    for item in value:
        yield from _coordinates(item)


def boundary_data(path: Path = BOUNDARY_PATH) -> tuple[list[dict], tuple[float, float, float, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data.get("features", [])
    geometries = [
        feature["geometry"]
        for feature in features
        if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
    ]
    points = [
        point
        for geometry in geometries
        for point in _coordinates(geometry.get("coordinates"))
    ]
    if not geometries or not points:
        raise ValueError(f"No polygon boundary geometry found in {path}.")
    bounds = (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )
    return geometries, bounds


def _write_clipped_worldcover(
    geometries: list[dict], output_path: Path, source: str = WORLD_COVER_URL
) -> None:
    with rasterio.open(source) as src:
        if src.crs is None:
            raise ValueError("WorldCover raster has no CRS.")
        projected = [transform_geom("EPSG:4326", src.crs, geometry) for geometry in geometries]
        clipped, transform = mask(src, projected, crop=True, filled=True)
        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            height=clipped.shape[1],
            width=clipped.shape[2],
            transform=transform,
            compress="deflate",
            predictor=2,
            tiled=True,
        )
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(clipped)


def _tile_index(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    count = 1 << zoom
    x = int((lon + 180.0) / 360.0 * count)
    lat = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * count)
    return min(count - 1, max(0, x)), min(count - 1, max(0, y))


def _download_terrarium_tile(x: int, y: int, zoom: int) -> np.ndarray:
    url = TERRARIUM_TILE_URL.format(z=zoom, x=x, y=y)
    request = urllib.request.Request(url, headers={"User-Agent": "Dedza-SDSS/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with MemoryFile(content) as memory_file:
            with memory_file.open() as tile:
                rgb = tile.read()
    if rgb.shape[0] < 3:
        raise ValueError(f"Unexpected Terrarium image format from {url}.")
    elevation = (rgb[0].astype(np.float32) * 256.0
                 + rgb[1].astype(np.float32)
                 + rgb[2].astype(np.float32) / 256.0
                 - 32768.0)
    if rgb.shape[0] >= 4:
        elevation[rgb[3] == 0] = DEM_NODATA
    return elevation


def _write_terrain(
    geometries: list[dict],
    bounds: tuple[float, float, float, float],
    dem_path: Path,
    slope_path: Path,
    zoom: int = TERRAIN_ZOOM,
) -> None:
    west, south, east, north = bounds
    x0, y1 = _tile_index(west, south, zoom)
    x1, y0 = _tile_index(east, north, zoom)
    tile_width = 256
    mosaic = np.full(
        ((y1 - y0 + 1) * tile_width, (x1 - x0 + 1) * tile_width),
        DEM_NODATA,
        dtype=np.float32,
    )
    tile_coordinates = [
        (tile_x, tile_y)
        for tile_y in range(y0, y1 + 1)
        for tile_x in range(x0, x1 + 1)
    ]
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_download_terrarium_tile, tile_x, tile_y, zoom): (tile_x, tile_y)
            for tile_x, tile_y in tile_coordinates
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            tile_x, tile_y = futures[future]
            elevation = future.result()
            if elevation.shape != (tile_width, tile_width):
                raise ValueError(
                    f"Unexpected terrain tile size at zoom {zoom}: {elevation.shape}."
                )
            row = (tile_y - y0) * tile_width
            column = (tile_x - x0) * tile_width
            mosaic[row:row + tile_width, column:column + tile_width] = elevation
            print(f"Downloaded terrain tile {completed}/{len(tile_coordinates)}", flush=True)

    pixel_size = MERCATOR_WORLD / (1 << zoom) / tile_width
    left = x0 / (1 << zoom) * MERCATOR_WORLD - MERCATOR_WORLD / 2
    top = MERCATOR_WORLD / 2 - y0 / (1 << zoom) * MERCATOR_WORLD
    transform = Affine(pixel_size, 0, left, 0, -pixel_size, top)
    projected = [
        transform_geom("EPSG:4326", "EPSG:3857", geometry)
        for geometry in geometries
    ]

    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=mosaic.shape[1],
            height=mosaic.shape[0],
            count=1,
            dtype="float32",
            crs="EPSG:3857",
            transform=transform,
            nodata=DEM_NODATA,
        ) as src:
            src.write(mosaic, 1)
            clipped, clipped_transform = mask(
                src, projected, crop=True, nodata=DEM_NODATA
            )
        dem_profile = {
            "driver": "GTiff",
            "width": clipped.shape[2],
            "height": clipped.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:3857",
            "transform": clipped_transform,
            "nodata": DEM_NODATA,
            "compress": "deflate",
            "predictor": 3,
            "tiled": True,
        }
        with rasterio.open(dem_path, "w", **dem_profile) as dst:
            dst.write(clipped)

    elevation = clipped[0]
    valid = np.isfinite(elevation) & (elevation != DEM_NODATA)
    ground_pixel = pixel_size * math.cos(math.radians((south + north) / 2))
    filled = np.where(valid, elevation, np.nan)
    with np.errstate(invalid="ignore"):
        gradient_y, gradient_x = np.gradient(filled, ground_pixel, ground_pixel)
        slope = np.degrees(np.arctan(np.hypot(gradient_x, gradient_y)))
    slope[~np.isfinite(slope) | ~valid] = SLOPE_NODATA
    slope_profile = dem_profile.copy()
    slope_profile.update(dtype="float32", nodata=SLOPE_NODATA)
    with rasterio.open(slope_path, "w", **slope_profile) as dst:
        dst.write(slope.astype(np.float32), 1)


def import_layers(
    boundary_path: Path = BOUNDARY_PATH,
    output_dir: Path = OUTPUT_DIR,
    skip_worldcover: bool = False,
    skip_terrain: bool = False,
) -> None:
    geometries, bounds = boundary_data(boundary_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not skip_worldcover:
        output = output_dir / "land_cover.tif"
        print(f"Clipping ESA WorldCover 2021 to Dedza -> {output}")
        _write_clipped_worldcover(geometries, output)
    if not skip_terrain:
        dem = output_dir / "terrain_dem.tif"
        slope = output_dir / "slope.tif"
        print(f"Downloading Mapzen Terrarium terrain tiles -> {dem}")
        _write_terrain(geometries, bounds, dem, slope)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary", type=Path, default=BOUNDARY_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--skip-worldcover", action="store_true")
    parser.add_argument("--skip-terrain", action="store_true")
    args = parser.parse_args()
    import_layers(
        boundary_path=args.boundary,
        output_dir=args.output_dir,
        skip_worldcover=args.skip_worldcover,
        skip_terrain=args.skip_terrain,
    )


if __name__ == "__main__":
    main()
