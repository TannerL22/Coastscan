import geopandas as gpd
import pytest
from shapely.geometry import LineString

from coastscan.coastline.segment import segment_breaks, segment_coastline


def frame(*lines: LineString) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"coastline_part_id": [f"part_{index}" for index in range(len(lines))]},
        geometry=list(lines),
        crs="EPSG:3857",
    )


def test_straight_kilometre_creates_four_segments() -> None:
    result = segment_coastline(
        frame(LineString([(0, 0), (1000, 0)])),
        region_id="r",
        coastline_version="v",
        target_length_m=250,
        minimum_length_m=75,
    )
    assert len(result) == 4
    assert result.segment_length_m.tolist() == pytest.approx([250] * 4)


def test_short_remainder_is_redistributed() -> None:
    breaks = segment_breaks(1050, 250, 75)
    assert len(breaks) == 4
    assert [end - start for start, end in breaks] == pytest.approx([262.5] * 4)


def test_curved_line_conserves_length() -> None:
    line = LineString([(0, 0), (100, 100), (250, 100), (400, 0)])
    result = segment_coastline(
        frame(line),
        region_id="r",
        coastline_version="v",
        target_length_m=100,
        minimum_length_m=30,
    )
    assert result.segment_length_m.sum() == pytest.approx(line.length)


def test_ids_and_geometry_are_reproducible() -> None:
    arguments = dict(
        coastline=frame(LineString([(0, 0), (700, 0)])),
        region_id="r",
        coastline_version="fixed",
        target_length_m=250,
        minimum_length_m=75,
    )
    first = segment_coastline(**arguments)
    second = segment_coastline(**arguments)
    assert first.segment_id.tolist() == second.segment_id.tolist()
    assert first.geometry.to_wkb().tolist() == second.geometry.to_wkb().tolist()


def test_separate_parts_remain_separate() -> None:
    result = segment_coastline(
        frame(LineString([(0, 0), (300, 0)]), LineString([(1000, 0), (1300, 0)])),
        region_id="r",
        coastline_version="v",
        target_length_m=250,
        minimum_length_m=75,
    )
    assert result.coastline_part_id.nunique() == 2
