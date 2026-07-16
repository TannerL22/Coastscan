import json
from pathlib import Path

import pytest

from coastscan.viewer.data import load_display_transects, load_viewer_data
from coastscan.viewer.layers import (
    build_deck,
    build_flag_layers,
    build_segment_layer,
    build_transect_layer,
)
from coastscan.viewer.metrics import metric_definition
from coastscan.viewer.selection import (
    preserve_selection,
    segment_id_from_pydeck_event,
    segment_id_from_table_event,
)


def test_segment_layer_retains_ids_metric_geometry_and_tooltip(viewer_project: Path) -> None:
    data = load_viewer_data("viewer_demo", viewer_project)
    metric = metric_definition("land_relief_100m_p90_m")
    assert metric is not None
    result = build_segment_layer(data.display_segments, metric, "robust")
    features = result.feature_collection["features"]
    assert len(features) == 12
    assert features[0]["id"] == "viewer_demo_segment_00"
    properties = features[0]["properties"]
    for field in (
        "segment_id",
        "selected_metric",
        "orientation_status",
        "terrain_quality",
        "bathymetry_screening",
        "first_valid_distance",
        "source_mismatch",
    ):
        assert field in properties
    assert features[0]["geometry"]["type"] == "LineString"
    assert data.display_segments.crs.to_epsg() == 4326


def test_projected_geometry_is_rejected_by_map_layer(viewer_project: Path) -> None:
    data = load_viewer_data("viewer_demo", viewer_project)
    metric = metric_definition("slope_p90_deg")
    assert metric is not None
    with pytest.raises(ValueError, match="EPSG:4326"):
        build_segment_layer(data.analytical_segments, metric, "full")


def test_transect_layer_can_be_selected_only_and_flags_build(viewer_project: Path) -> None:
    data = load_viewer_data("viewer_demo", viewer_project)
    transects = load_display_transects(data, {"viewer_demo_segment_00"})
    layer = build_transect_layer(transects)
    assert layer is not None
    encoded = json.loads(layer.to_json())
    assert encoded["id"] == "bathymetry-transects"
    assert len(encoded["data"]) == 2
    flags = build_flag_layers(
        data.display_segments,
        {"ambiguous", "source_mismatch", "large_coastal_gap", "missing_bathymetry"},
    )
    assert {json.loads(item.to_json())["id"] for item in flags} == {
        "flag-ambiguous",
        "flag-large_coastal_gap",
        "flag-missing_bathymetry",
        "flag-source_mismatch",
    }


def test_deck_defaults_to_no_secret_carto_and_supports_categorical(viewer_project: Path) -> None:
    data = load_viewer_data("viewer_demo", viewer_project)
    metric = metric_definition("bathymetry_screening_class")
    assert metric is not None
    deck, scale = build_deck(data.display_segments, metric, "robust")
    assert scale is None
    encoded = json.loads(deck.to_json())
    assert encoded["mapProvider"] == "carto"
    assert "apiKeys" not in encoded or not encoded["apiKeys"]


def test_selection_parsers_and_preservation() -> None:
    event = {
        "selection": {
            "objects": {
                "coastline-segments": [{"properties": {"segment_id": "viewer_demo_segment_04"}}]
            }
        }
    }
    assert segment_id_from_pydeck_event(event) == "viewer_demo_segment_04"
    table = load_viewer_data  # ensure selection helper is independent of GeoPandas
    del table
    import pandas as pd

    visible = pd.DataFrame({"segment_id": ["a", "b"]})
    assert segment_id_from_table_event({"selection": {"rows": [1]}}, visible) == "b"
    assert preserve_selection("b", {"a", "b"}) == "b"
    assert preserve_selection("c", {"a", "b"}) is None
