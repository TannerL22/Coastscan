import geopandas as gpd
import pytest
from shapely.geometry import LineString

from coastscan.coastline.orientation import orient_segments
from coastscan.coastline.segment import segment_coastline
from coastscan.coastline.transects import generate_transects


def oriented(line: LineString):
    coast = gpd.GeoDataFrame({"coastline_part_id": ["p"]}, geometry=[line], crs="EPSG:3857")
    segments = segment_coastline(
        coast,
        region_id="r",
        coastline_version="v",
        target_length_m=1000,
        minimum_length_m=10,
    )
    return orient_segments(segments, line.buffer(100, single_sided=True), 10, [])


def test_spacing_lengths_directions_and_origins() -> None:
    segments = oriented(LineString([(0, 0), (100, 0)]))
    result = generate_transects(segments, 25, 100, 200)
    assert len(result) == 10
    assert set(result.direction) == {"inland", "offshore"}
    assert result.loc[result.direction == "inland"].length.tolist() == pytest.approx([100] * 5)
    assert result.loc[result.direction == "offshore"].length.tolist() == pytest.approx([200] * 5)
    assert all(
        segments.geometry.iloc[0].distance(__import__("shapely").geometry.Point(line.coords[0]))
        < 1e-8
        for line in result.geometry
    )


def test_curved_segment_uses_local_bearings() -> None:
    segments = oriented(LineString([(0, 0), (50, 0), (100, 50)]))
    result = generate_transects(segments, 25, 50, 50)
    assert result.loc[result.direction == "inland", "bearing_deg"].nunique() > 1


def test_ambiguous_segment_produces_typed_empty_transects() -> None:
    segments = oriented(LineString([(0, 0), (100, 0)]))
    segments["orientation_status"] = "ambiguous"
    result = generate_transects(segments, 25, 50, 50)
    assert result.empty
    assert "geometry" in result.columns
