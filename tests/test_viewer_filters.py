from pathlib import Path

from coastscan.viewer.data import load_viewer_data
from coastscan.viewer.filters import apply_filters, reset_filter_state
from coastscan.viewer.models import ViewerFilters


def test_terrain_categorical_boolean_and_search_filters(viewer_project: Path) -> None:
    frame = load_viewer_data("viewer_demo", viewer_project).display_segments
    terrain = apply_filters(frame, ViewerFilters(relief_100m_range=(30, None)))
    assert len(terrain) and (terrain.land_relief_100m_p90_m >= 30).all()
    ambiguous = apply_filters(frame, ViewerFilters(orientation_statuses=frozenset({"ambiguous"})))
    assert ambiguous.segment_id.tolist() == ["viewer_demo_segment_02"]
    mismatch = apply_filters(frame, ViewerFilters(source_mismatch=True))
    assert mismatch.segment_id.tolist() == ["viewer_demo_segment_07"]
    search = apply_filters(frame, ViewerFilters(segment_search="SEGMENT_01"))
    assert search.segment_id.tolist() == ["viewer_demo_segment_01"]


def test_availability_and_bathymetry_filters(viewer_project: Path) -> None:
    frame = load_viewer_data("viewer_demo", viewer_project).display_segments
    terrain_missing = apply_filters(frame, ViewerFilters(terrain_availability="missing"))
    assert terrain_missing.segment_id.tolist() == ["viewer_demo_segment_03"]
    bathymetry_missing = apply_filters(frame, ViewerFilters(bathymetry_availability="missing"))
    assert set(bathymetry_missing.segment_id) == {
        "viewer_demo_segment_02",
        "viewer_demo_segment_04",
    }
    background = apply_filters(
        frame,
        ViewerFilters(bathymetry_screening_classes=frozenset({"background_only"})),
    )
    assert set(background.segment_id) == {
        "viewer_demo_segment_05",
        "viewer_demo_segment_06",
        "viewer_demo_segment_07",
    }
    depth = apply_filters(
        frame,
        ViewerFilters(depth_field="depth_500m_p50_m", depth_range=(20, 30)),
    )
    assert depth.depth_500m_p50_m.between(20, 30).all()
    gradient = apply_filters(
        frame,
        ViewerFilters(gradient_field="gradient_250_1000m_p50", gradient_range=(-0.005, 0.015)),
    )
    assert gradient.gradient_250_1000m_p50.between(-0.005, 0.015).all()
    fallback = apply_filters(frame, ViewerFilters(maximum_global_fallback_share=0.5))
    assert "viewer_demo_segment_06" not in set(fallback.segment_id)


def test_combined_filter_empty_result_and_source_immutability(viewer_project: Path) -> None:
    frame = load_viewer_data("viewer_demo", viewer_project).display_segments
    original_ids = frame.segment_id.tolist()
    combined = apply_filters(
        frame,
        ViewerFilters(
            orientation_statuses=frozenset({"resolved"}),
            minimum_terrain_valid_share=0.8,
            minimum_bathymetry_valid_share=0.7,
            source_mismatch=False,
        ),
    )
    assert len(combined) < len(frame)
    assert frame.segment_id.tolist() == original_ids
    empty = apply_filters(
        frame,
        ViewerFilters(segment_search="not-a-segment", minimum_terrain_valid_share=1.0),
    )
    assert empty.empty


def test_reset_state_removes_dynamic_filter_keys() -> None:
    state = {
        "filter_search": "abc",
        "filter_depth_range": (1.0, 2.0),
        "viewer_metric": "slope_p90_deg",
    }
    reset_filter_state(state)
    assert "filter_search" not in state
    assert "filter_depth_range" not in state
    assert state["viewer_metric"] == "slope_p90_deg"
