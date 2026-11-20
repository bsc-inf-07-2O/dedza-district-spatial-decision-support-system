"""Download and filter Microsoft GlobalMLBuildingFootprints to Dedza."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from .site_constraints import geometry_bounds
else:
    from site_constraints import geometry_bounds

from shapely.geometry import shape, mapping
from shapely.ops import unary_union

BOUNDARY_PATH = ROOT / "data" / "raw" / "dedza_boundary.geojson"
OUTPUT_PATH = ROOT / "data" / "processed" / "site_constraints.sqlite"
DATASET_INDEX_URL = (
    "https://bfppub.blob.core.windows.net/$web/2026-08-13/dataset-links.csv"
)
SOURCE_NAME = "Microsoft Global ML Building Footprints"
SOURCE_DATE = "2026-08-13"
SOURCE_LICENSE = "CDLA Permissive 2.0"


def quadkey_bounds(quadkey: str) -> tuple[float, float, float, float]:
    zoom = len(quadkey)
    tile_x = 0
    tile_y = 0
    for index, character in enumerate(quadkey):
        bit = 1 << (zoom - index - 1)
        digit = int(character)
        if digit not in (0, 1, 2, 3):
            raise ValueError(f"Invalid quadkey: {quadkey}")
        if digit & 1:
            tile_x |= bit
        if digit & 2:
            tile_y |= bit

    scale = 1 << zoom

    def longitude(x: int) -> float:
        return x / scale * 360 - 180

    def latitude(y: int) -> float:
        import math

        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))

    return (
        longitude(tile_x),
        latitude(tile_y + 1),
        longitude(tile_x + 1),
        latitude(tile_y),
    )


def read_boundary(path: Path) -> tuple[list[dict], tuple[float, float, float, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    geometries = [
        feature["geometry"]
        for feature in data.get("features", [])
        if feature.get("geometry")
    ]
    if not geometries:
        raise ValueError(f"No district boundary geometry found in {path}.")
    bounds = (
        min(geometry_bounds(geometry)[0] for geometry in geometries),
        min(geometry_bounds(geometry)[1] for geometry in geometries),
        max(geometry_bounds(geometry)[2] for geometry in geometries),
        max(geometry_bounds(geometry)[3] for geometry in geometries),
    )
    return geometries, bounds


def read_malawi_index(url: str = DATASET_INDEX_URL) -> list[dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "DD-SDSS/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        rows = csv.DictReader(io.TextIOWrapper(response, encoding="utf-8"))
        return [row for row in rows if row.get("Location") == "Malawi"]


def overlapping_tiles(
    rows: list[dict[str, str]],
    bounds: tuple[float, float, float, float],
) -> list[dict[str, str]]:
    west, south, east, north = bounds
    matches = []
    for row in rows:
        tile_west, tile_south, tile_east, tile_north = quadkey_bounds(row["QuadKey"])
        if (
            tile_east >= west
            and tile_west <= east
            and tile_north >= south
            and tile_south <= north
        ):
            matches.append(row)
    if not matches:
        raise ValueError("No Microsoft building-footprint tiles overlap Dedza.")
    return matches


def feature_bounds_intersect(
    feature_bounds: tuple[float, float, float, float],
    district_bounds: tuple[float, float, float, float],
) -> bool:
    west, south, east, north = feature_bounds
    dwest, dsouth, deast, dnorth = district_bounds
    return east >= dwest and west <= deast and north >= dsouth and south <= dnorth


def write_database(
    output_path: Path,
    geometries: list[dict],
    district_bounds: tuple[float, float, float, float],
    tiles: list[dict[str, str]],
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.stem + ".sqlite.tmp")
    con = sqlite3.connect(temp_path)
    try:
        existing_tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "metadata" not in existing_tables:
            con.executescript(
                """
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE imported_tiles (
                    quadkey TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL
                );
                CREATE TABLE buildings (
                    id INTEGER PRIMARY KEY,
                    quadkey TEXT NOT NULL,
                    min_lon REAL NOT NULL,
                    min_lat REAL NOT NULL,
                    max_lon REAL NOT NULL,
                    max_lat REAL NOT NULL,
                    height REAL,
                    confidence REAL,
                    geometry TEXT NOT NULL
                );
                CREATE INDEX buildings_bounds_idx
                    ON buildings(min_lon, max_lon, min_lat, max_lat);
                """
            )
            con.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                [
                    ("buildings_source", SOURCE_NAME),
                    ("buildings_date", SOURCE_DATE),
                    ("buildings_license", SOURCE_LICENSE),
                    ("buildings_tiles", json.dumps([row["QuadKey"] for row in tiles])),
                ],
            )
            con.commit()

        completed_tiles = {
            row[0] for row in con.execute("SELECT quadkey FROM imported_tiles")
        }
        imported = con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
        district = unary_union([shape(geometry) for geometry in geometries])
        insert = """
            INSERT INTO buildings (
                quadkey, min_lon, min_lat, max_lon, max_lat,
                height, confidence, geometry
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        pending_tiles = [tile for tile in tiles if tile["QuadKey"] not in completed_tiles]
        for tile_index, tile in enumerate(pending_tiles, start=1):
            print(
                f"Downloading tile {tile_index}/{len(pending_tiles)} "
                f"(quadkey {tile['QuadKey']})...",
                flush=True,
            )
            processed_in_tile = 0
            request = urllib.request.Request(
                tile["Url"], headers={"User-Agent": "DD-SDSS/1.0"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                with gzip.GzipFile(fileobj=response) as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        feature = json.loads(line)
                        geometry = feature.get("geometry")
                        if not geometry:
                            continue
                        building = shape(geometry)
                        bounds = building.bounds
                        if not feature_bounds_intersect(bounds, district_bounds):
                            continue
                        clipped = building.intersection(district)
                        if clipped.is_empty:
                            continue
                        clipped_geometry = mapping(clipped)
                        clipped_bounds = clipped.bounds
                        properties = feature.get("properties") or {}
                        con.execute(
                            insert,
                            (
                                tile["QuadKey"],
                                clipped_bounds[0],
                                clipped_bounds[1],
                                clipped_bounds[2],
                                clipped_bounds[3],
                                properties.get("height"),
                                properties.get("confidence"),
                                json.dumps(clipped_geometry, separators=(",", ":")),
                            ),
                        )
                        imported += 1
                        processed_in_tile += 1
                        if processed_in_tile % 500 == 0:
                            con.commit()
                            print(
                                f"  {processed_in_tile:,} intersecting records processed "
                                f"in this tile; {imported:,} clipped footprints stored.",
                                flush=True,
                            )
            con.execute(
                "INSERT INTO imported_tiles (quadkey, completed_at) VALUES (?, datetime('now'))",
                (tile["QuadKey"],),
            )
            con.commit()
            print(
                f"  Completed quadkey {tile['QuadKey']}; "
                f"{imported:,} footprints stored so far.",
                flush=True,
            )
        con.close()
        os.replace(temp_path, output_path)
        return imported
    except Exception:
        con.close()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary", type=Path, default=BOUNDARY_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    boundary_path = args.boundary.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    if not boundary_path.is_file():
        parser.error(f"Dedza boundary not found: {boundary_path}. Build the main database first.")

    geometries, bounds = read_boundary(boundary_path)
    tiles = overlapping_tiles(read_malawi_index(), bounds)
    print(
        f"Importing {len(tiles)} Microsoft footprint tiles "
        f"({', '.join(row['QuadKey'] for row in tiles)})..."
    )
    count = write_database(output_path, geometries, bounds, tiles)
    print(f"Imported {count:,} building footprints to {output_path}.")


if __name__ == "__main__":
    main()
