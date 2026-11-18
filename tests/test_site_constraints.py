import pytest

from scripts.import_buildings import overlapping_tiles, quadkey_bounds


def test_quadkey_bounds_decode_malawi_tile():
    west, south, east, north = quadkey_bounds("300130200")

    assert west == pytest.approx(33.75)
    assert south == pytest.approx(-14.604847, abs=1e-5)
    assert east == pytest.approx(34.453125)
    assert north == pytest.approx(-13.923404, abs=1e-5)


def test_overlapping_tiles_select_only_intersecting_tiles():
    rows = [
        {"QuadKey": "300130200", "Url": "inside"},
        {"QuadKey": "300121313", "Url": "outside"},
    ]

    result = overlapping_tiles(rows, (34.0, -14.4, 34.2, -14.1))

    assert [row["Url"] for row in result] == ["inside"]


def test_overlapping_tiles_fails_when_region_is_not_covered():
    with pytest.raises(ValueError, match="No Microsoft building-footprint tiles"):
        overlapping_tiles([], (34.0, -14.4, 34.2, -14.1))
