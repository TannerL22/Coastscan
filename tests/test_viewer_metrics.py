import numpy as np
import pandas as pd

from coastscan.viewer.formatting import deterministic_interpretation, format_value
from coastscan.viewer.layers import (
    CATEGORICAL_COLORS,
    MISSING_COLOR,
    categorical_color,
    continuous_color,
    continuous_scale,
)
from coastscan.viewer.metrics import (
    METRIC_REGISTRY,
    available_metrics,
    metric_definition,
    validate_registry,
)


def test_metric_registry_is_complete_and_unknown_is_safe() -> None:
    assert not validate_registry()
    assert metric_definition("does_not_exist") is None
    assert all(definition.display_name for definition in METRIC_REGISTRY.values())
    assert all(definition.unit is not None for definition in METRIC_REGISTRY.values())


def test_available_metrics_excludes_missing_and_bathymetry_when_disabled() -> None:
    columns = ["slope_p90_deg", "depth_500m_p50_m", "orientation_status"]
    fields = {item.field_name for item in available_metrics(columns)}
    assert fields == set(columns)
    terrain_only = {
        item.field_name for item in available_metrics(columns, include_bathymetry=False)
    }
    assert terrain_only == {"slope_p90_deg", "orientation_status"}
    assert metric_definition("orientation_status").kind == "categorical"  # type: ignore[union-attr]
    assert metric_definition("orientation_source_mismatch_flag").kind == "boolean"  # type: ignore[union-attr]
    assert metric_definition("clarity_percentile_p50").category == "optical"  # type: ignore[union-attr]


def test_continuous_scales_cover_robust_full_constant_missing_and_negative() -> None:
    values = pd.Series([-10.0, -2.0, 0.0, 3.0, 1000.0, np.nan])
    robust = continuous_scale(values, "robust", diverging=True)
    full = continuous_scale(values, "full", diverging=True)
    assert robust.minimum == -robust.maximum  # type: ignore[operator]
    assert full.minimum == -1000
    assert full.maximum == 1000
    assert continuous_color(np.nan, robust, diverging=True) == MISSING_COLOR
    constant = continuous_scale(pd.Series([2.5, 2.5, np.nan]), "robust")
    assert constant.constant
    assert continuous_color(2.5, constant)
    missing = continuous_scale(pd.Series([np.nan]), "full")
    assert missing.valid_count == 0 and missing.minimum is None


def test_categorical_styles_avoid_automatic_red_green_semantics() -> None:
    assert categorical_color("orientation_status", "ambiguous")
    all_colors = [color[:3] for values in CATEGORICAL_COLORS.values() for color in values.values()]
    assert [255, 0, 0] not in all_colors
    assert [0, 255, 0] not in all_colors


def test_deterministic_interpretation_states_limits_and_has_no_banned_terms() -> None:
    row = pd.Series(
        {
            "land_relief_100m_p90_m": 42.0,
            "terrain_valid_sample_share": 0.9,
            "bathymetry_valid_transect_share": 0.8,
            "gradient_250_1000m_p50": 0.02,
            "bathymetry_screening_class": "background_only",
            "bathymetry_native_resolution_m": 115.0,
        }
    )
    first = deterministic_interpretation(row)
    assert first == deterministic_interpretation(row)
    assert "background-only" in first
    assert "115 m" in first
    assert "submerged obstacles" in first
    banned = {"safe", "unsafe", "suitable", "recommended", "jumpable"}
    assert not banned & {word.strip(".,").casefold() for word in first.split()}

    missing = deterministic_interpretation(pd.Series(dtype=object))
    assert "Terrestrial morphology is missing" in missing
    assert "Regional bathymetry is unavailable" in missing


def test_value_formatting_handles_missing_and_categorical() -> None:
    depth = metric_definition("depth_500m_p50_m")
    orientation = metric_definition("orientation_status")
    assert format_value(12.345, depth) == "12.35 m"
    assert format_value(np.nan, depth) == "Not available"
    assert format_value("resolved_fallback", orientation) == "Resolved Fallback"
