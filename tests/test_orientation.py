import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from coastscan.coastline.orientation import orient_segments
from coastscan.coastline.segment import segment_coastline


def one_segment(line: LineString):
    coast = gpd.GeoDataFrame({"coastline_part_id": ["p"]}, geometry=[line], crs="EPSG:3857")
    return segment_coastline(
        coast,
        region_id="r",
        coastline_version="v",
        target_length_m=1000,
        minimum_length_m=10,
    )


def test_land_on_left_and_right() -> None:
    line = LineString([(0, 0), (100, 0)])
    left = orient_segments(one_segment(line), box(-10, 0, 110, 100), 10, [])
    right = orient_segments(one_segment(line), box(-10, -100, 110, 0), 10, [])
    assert left.landward_bearing_deg.iloc[0] == 0
    assert right.landward_bearing_deg.iloc[0] == 180


def test_ambiguous_case_is_not_guessed() -> None:
    result = orient_segments(
        one_segment(LineString([(0, 0), (100, 0)])), box(-10, -50, 110, 50), 10, [20]
    )
    assert result.orientation_status.iloc[0] == "ambiguous"
    assert result.orientation_attempts.iloc[0] == [10.0, 20.0]


def test_fallback_resolves_deterministically() -> None:
    segments = one_segment(LineString([(0, 0), (100, 0)]))
    land = box(-10, 20, 110, 100)
    first = orient_segments(segments, land, 10, [40])
    second = orient_segments(segments, land, 10, [40])
    assert first.orientation_status.iloc[0] == "resolved_fallback"
    assert first.landward_bearing_deg.tolist() == second.landward_bearing_deg.tolist()


def test_offset_direct_coastline_records_source_mismatch() -> None:
    segments = one_segment(LineString([(0, -2), (100, -2)]))
    result = orient_segments(
        segments,
        box(-10, 0, 110, 100),
        10,
        [],
        [-20, 0, 20],
        source_mismatch_tolerance_m=1,
    )
    assert result.orientation_status.iloc[0] == "resolved"
    assert result.orientation_method.iloc[0] == "multi_point_vote"
    assert result.orientation_source_mismatch_flag.iloc[0]
    assert result.coast_to_landmask_boundary_distance_m.iloc[0] == pytest.approx(2)


def test_multi_point_voting_ignores_one_local_anomaly() -> None:
    segments = one_segment(LineString([(0, 0), (100, 0)]))
    land = box(-20, 0, 120, 100).difference(box(45, 0, 55, 30))
    result = orient_segments(segments, land, 10, [], [-30, 0, 30])
    assert result.orientation_status.iloc[0] == "resolved"
    assert result.orientation_method.iloc[0] == "multi_point_vote"
    assert result.orientation_vote_count_landward.iloc[0] == 2


def test_boundary_coincident_direct_coastline_resolves() -> None:
    segments = one_segment(LineString([(0, 0), (100, 0)]))
    result = orient_segments(segments, box(-10, 0, 110, 100), 10, [], [-20, 0, 20])
    assert result.orientation_status.iloc[0] == "resolved"
    assert not result.orientation_source_mismatch_flag.iloc[0]
