"""Download and clip the population-growth and flood-hazard layers for Dedza.

Outputs (in data/raw, clipped to the Dedza extent plus a small margin):
  * dedza_pop_2020_CN_100m_R2025A_v1.tif and dedza_pop_2030_CN_100m_R2025A_v1.tif
    WorldPop Global 2015-2030 R2025A constrained population (CC BY 4.0), the same
    series as the 2025 raster. 2030 is WorldPop's projection; with 2025 it gives
    per-cell projected growth, and 2020 gives the recent trend.
  * dedza_flood_rp100_depth.tif
    JRC / Copernicus EMS GloFAS global river flood hazard map v2.1, water depth (m)
    for a 1-in-100-year flood, ~90 m (ODbL 1.0). Not an official national flood map.
  * dedza_permanent_water.tif
    The permanent water bodies (lakes, large rivers) used with that hazard map.

Run once (needs internet), then rebuild: python scripts/build_db.py
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
BOUNDARY_PATH = RAW / "dedza_boundary.geojson"
MARGIN_DEG = 0.05

WORLDPOP_URL = (
    "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/{year}/MWI/v1/100m/"
    "constrained/mwi_pop_{year}_CN_100m_R2025A_v1.tif"
)
FLOOD_BASE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard"
FLOOD_TILE = "ID152_S10_E30"  # 10 x 10 degree tile covering 30-40 E, 10-20 S

LAYERS = {
    "dedza_pop_2020_CN_100m_R2025A_v1.tif": WORLDPOP_URL.format(year=2020),
    "dedza_pop_2030_CN_100m_R2025A_v1.tif": WORLDPOP_URL.format(year=2030),
    "dedza_flood_rp100_depth.tif": f"{FLOOD_BASE}/RP100/{FLOOD_TILE}_RP100_depth.tif",
    "dedza_permanent_water.tif": f"{FLOOD_BASE}/Permanent_WaterBodies/{FLOOD_TILE}_permanent_water.tif",
}


def dedza_bounds() -> tuple[float, float, float, float]:
    data = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    xs, ys = [], []

    def walk(value: object) -> None:
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            xs.append(float(value[0]))
            ys.append(float(value[1]))
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for feature in data["features"]:
        walk(feature["geometry"]["coordinates"])
    return (min(xs) - MARGIN_DEG, min(ys) - MARGIN_DEG, max(xs) + MARGIN_DEG, max(ys) + MARGIN_DEG)


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "dd-sdss-import"})
    with urllib.request.urlopen(request, timeout=300) as response, target.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


def clip(source_path: Path, output_path: Path, bounds: tuple[float, float, float, float]) -> None:
    with rasterio.open(source_path) as source:
        if source.crs is None or source.crs.to_epsg() != 4326:
            raise ValueError(f"{source_path.name}: expected EPSG:4326, found {source.crs}")
        window = from_bounds(*bounds, transform=source.transform).round_offsets().round_lengths()
        data = source.read(window=window)
        profile = source.profile.copy()
        profile.update(
            width=window.width,
            height=window.height,
            transform=source.window_transform(window),
            compress="deflate",
            tiled=False,
            blockxsize=None,
            blockysize=None,
        )
        profile.pop("blockxsize")
        profile.pop("blockysize")
    with rasterio.open(output_path, "w", **profile) as out:
        out.write(data)


def main() -> None:
    bounds = dedza_bounds()
    RAW.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, url in LAYERS.items():
            print(f"Downloading {url}")
            downloaded = Path(tmp) / Path(url).name
            download(url, downloaded)
            clip(downloaded, RAW / name, bounds)
            downloaded.unlink()
            print(f"  wrote {RAW / name} ({(RAW / name).stat().st_size / 1e6:.1f} MB)")
    print("Done. Rebuild the database: python scripts/build_db.py --source-dir <Lab 4 folder>")


if __name__ == "__main__":
    main()
