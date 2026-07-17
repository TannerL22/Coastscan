"""Deterministic PyDeck layer and colour-scale construction."""

import math
from dataclasses import dataclass
from typing import Any, Literal, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import pydeck as pdk
from shapely.geometry import mapping

from coastscan.viewer.formatting import format_value
from coastscan.viewer.metrics import metric_definition
from coastscan.viewer.models import ColorScale, MetricDefinition, ScaleMode
from coastscan.viewer.validation import (
    line_parts,
    validate_display_line_geometry,
)

MISSING_COLOR = [150, 155, 165, 210]
SEQUENTIAL_PALETTE = [
    [68, 1, 84, 230],
    [59, 82, 139, 230],
    [33, 145, 140, 230],
    [94, 201, 98, 230],
    [253, 231, 37, 230],
]
DIVERGING_PALETTE = [
    [70, 70, 140, 230],
    [119, 136, 176, 230],
    [205, 205, 205, 230],
    [205, 151, 78, 230],
    [135, 82, 35, 230],
]
CATEGORICAL_COLORS: dict[str, dict[str, list[int]]] = {
    "orientation_status": {
        "resolved": [48, 122, 170, 230],
        "resolved_fallback": [128, 88, 170, 230],
        "ambiguous": [230, 159, 0, 240],
        "invalid_geometry": [95, 95, 105, 230],
    },
    "bathymetry_screening_class": {
        "local_morphology_candidate": [35, 139, 148, 230],
        "coastal_context": [67, 119, 170, 230],
        "regional_screening": [128, 88, 170, 230],
        "background_only": [145, 145, 155, 230],
        "insufficient": [230, 159, 0, 230],
    },
    "terrain_quality_flag": {
        "good": [55, 126, 184, 230],
        "partial": [180, 150, 70, 230],
        "insufficient": [170, 105, 55, 230],
        "outside_dem": [115, 115, 125, 230],
    },
    "bathymetry_quality_flag": {
        "usable_with_resolution_limits": [48, 122, 170, 230],
        "partial": [180, 150, 70, 230],
        "insufficient": [145, 105, 95, 230],
    },
    "orientation_source_mismatch_flag": {
        "true": [190, 92, 172, 240],
        "false": [80, 135, 170, 220],
    },
}


@dataclass(frozen=True)
class SegmentLayerResult:
    layer: pdk.Layer
    scale: ColorScale | None
    feature_collection: dict[str, Any]
    path_records: list[dict[str, object]]


def continuous_scale(
    values: pd.Series,
    mode: ScaleMode,
    *,
    diverging: bool = False,
) -> ColorScale:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    finite = numeric[np.isfinite(numeric)]
    if not len(finite):
        return ColorScale(None, None, mode, False, 0, 0.0 if diverging else None)
    if mode == "robust" and len(finite) > 1:
        minimum, maximum = float(finite.quantile(0.05)), float(finite.quantile(0.95))
    else:
        minimum, maximum = float(finite.min()), float(finite.max())
    constant = math.isclose(minimum, maximum, rel_tol=1e-12, abs_tol=1e-12)
    midpoint = 0.0 if diverging else None
    if diverging:
        extent = max(abs(minimum), abs(maximum))
        if extent > 0:
            minimum, maximum = -extent, extent
    return ColorScale(minimum, maximum, mode, constant, len(finite), midpoint)


def _interpolate_palette(palette: list[list[int]], fraction: float) -> list[int]:
    clipped = min(1.0, max(0.0, fraction))
    position = clipped * (len(palette) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(palette) - 1)
    weight = position - lower
    return [
        int(round(palette[lower][channel] * (1 - weight) + palette[upper][channel] * weight))
        for channel in range(4)
    ]


def continuous_color(value: object, scale: ColorScale, *, diverging: bool = False) -> list[int]:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return MISSING_COLOR
    if not math.isfinite(numeric) or scale.minimum is None or scale.maximum is None:
        return MISSING_COLOR
    fraction = (
        0.5 if scale.constant else (numeric - scale.minimum) / (scale.maximum - scale.minimum)
    )
    return _interpolate_palette(DIVERGING_PALETTE if diverging else SEQUENTIAL_PALETTE, fraction)


def categorical_color(field_name: str, value: object) -> list[int]:
    if value is None or bool(pd.isna(cast(Any, value))):
        return MISSING_COLOR
    key = str(value).casefold() if not isinstance(value, bool) else str(value).lower()
    return CATEGORICAL_COLORS.get(field_name, {}).get(key, [92, 120, 150, 225])


def _json_value(value: object) -> object:
    if value is None:
        return None
    try:
        if bool(pd.isna(cast(Any, value))):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if isinstance(value, np.generic) else value


def _segment_properties(
    row: pd.Series,
    metric: MetricDefinition,
    color: list[int],
    selected_segment_id: str | None,
) -> dict[str, object]:
    segment_id = str(row.segment_id)
    return {
        "segment_id": segment_id,
        "selected_metric": _json_value(row.get(metric.field_name)),
        "metric_label": metric.display_name,
        "metric_value": format_value(row.get(metric.field_name), metric),
        "orientation_status": _json_value(row.get("orientation_status")) or "Not recorded",
        "terrain_quality": _json_value(row.get("terrain_quality_flag")) or "Not available",
        "bathymetry_screening": _json_value(row.get("bathymetry_screening_class"))
        or "Not available",
        "first_valid_distance": format_value(
            row.get("bathymetry_first_valid_distance_p50_m"),
            metric_definition("bathymetry_first_valid_distance_p50_m"),
        ),
        "source_mismatch": "Yes"
        if bool(row.get("orientation_source_mismatch_flag", False))
        else "No",
        "display_color": color,
        "line_width": 8 if segment_id == selected_segment_id else 5,
    }


def build_segment_layer(
    frame: gpd.GeoDataFrame,
    metric: MetricDefinition,
    scale_mode: ScaleMode,
    *,
    selected_segment_id: str | None = None,
) -> SegmentLayerResult:
    validate_display_line_geometry(frame, label="Coastline segment layer")
    scale: ColorScale | None = None
    if metric.kind == "continuous":
        scale = continuous_scale(
            frame[metric.field_name],
            scale_mode,
            diverging=metric.recommended_scale == "diverging",
        )
    features: list[dict[str, object]] = []
    path_records: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        color = (
            continuous_color(
                row.get(metric.field_name),
                scale,
                diverging=metric.recommended_scale == "diverging",
            )
            if scale is not None
            else categorical_color(metric.field_name, row.get(metric.field_name))
        )
        properties = _segment_properties(row, metric, color, selected_segment_id)
        features.append(
            {
                "type": "Feature",
                "id": str(row.segment_id),
                "geometry": mapping(row.geometry),
                "properties": properties,
            }
        )
        for component_index, part in enumerate(line_parts(row.geometry)):
            path_records.append(
                {
                    **properties,
                    "component_index": component_index,
                    "path": [[float(x), float(y)] for x, y, *_ in part.coords],
                }
            )
    collection: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    layer = pdk.Layer(
        "PathLayer",
        id="coastline-segments",
        data=path_records,
        pickable=True,
        auto_highlight=True,
        highlight_color=[255, 255, 255, 230],
        get_path="path",
        get_color="display_color",
        get_width="line_width",
        width_units="pixels",
        width_min_pixels=3,
        cap_rounded=True,
        joint_rounded=True,
    )
    return SegmentLayerResult(layer, scale, collection, path_records)


def _flag_mask(frame: gpd.GeoDataFrame, flag: str) -> pd.Series:
    def series(field: str, default: object = None) -> pd.Series:
        if field in frame:
            return pd.Series(frame[field], index=frame.index)
        return pd.Series(default, index=frame.index)

    if flag == "ambiguous":
        return pd.Series(series("orientation_status").eq("ambiguous"), index=frame.index)
    if flag == "source_mismatch":
        return pd.Series(
            series("orientation_source_mismatch_flag", False).fillna(False).astype(bool),
            index=frame.index,
        )
    if flag == "large_coastal_gap":
        values = pd.to_numeric(series("bathymetry_large_coastal_gap_share"), errors="coerce")
        return pd.Series(values.fillna(0) > 0, index=frame.index)
    if flag == "missing_bathymetry":
        values = pd.to_numeric(series("bathymetry_valid_transect_share"), errors="coerce")
        return pd.Series(values.isna() | (values <= 0), index=frame.index)
    if flag == "missing_terrain":
        values = pd.to_numeric(series("terrain_valid_sample_share"), errors="coerce")
        return pd.Series(values.isna() | (values <= 0), index=frame.index)
    if flag == "global_fallback":
        fallback = pd.to_numeric(series("global_fallback_source_share"), errors="coerce")
        screening = series("bathymetry_screening_class")
        return pd.Series(
            (fallback.fillna(0) >= 0.5) | screening.eq("background_only"),
            index=frame.index,
        )
    return pd.Series(False, index=frame.index)


FLAG_COLORS = {
    "ambiguous": [230, 159, 0, 245],
    "source_mismatch": [190, 92, 172, 245],
    "large_coastal_gap": [201, 118, 44, 245],
    "missing_bathymetry": [105, 105, 115, 245],
    "missing_terrain": [120, 95, 155, 245],
    "global_fallback": [80, 150, 165, 245],
}


def build_flag_layers(frame: gpd.GeoDataFrame, enabled_flags: set[str]) -> list[pdk.Layer]:
    validate_display_line_geometry(frame, label="Coastline flag layers")
    layers: list[pdk.Layer] = []
    for flag in sorted(enabled_flags):
        subset = frame.loc[_flag_mask(frame, flag)]
        if subset.empty:
            continue
        records = [
            {
                "segment_id": str(row.segment_id),
                "component_index": component_index,
                "flag": flag,
                "path": [[float(x), float(y)] for x, y, *_ in part.coords],
            }
            for _, row in subset.iterrows()
            for component_index, part in enumerate(line_parts(row.geometry))
        ]
        layers.append(
            pdk.Layer(
                "PathLayer",
                id=f"flag-{flag}",
                data=records,
                pickable=False,
                get_path="path",
                get_color=FLAG_COLORS[flag],
                get_width=9,
                width_units="pixels",
                width_min_pixels=8,
                opacity=0.7,
                cap_rounded=True,
                joint_rounded=True,
            )
        )
    return layers


def build_transect_layer(transects: gpd.GeoDataFrame) -> pdk.Layer | None:
    if transects.empty:
        return None
    validate_display_line_geometry(
        transects,
        label="Bathymetry transect layer",
        maximum_jump_km=5.0,
    )
    records: list[dict[str, object]] = []
    for _, row in transects.iterrows():
        coords = [[float(x), float(y)] for x, y in row.geometry.coords]
        status = str(row.get("bathymetry_origin_status", "unknown"))
        color = [120, 125, 135, 95] if status == "no_valid_bathymetry" else [55, 135, 175, 105]
        records.append(
            {
                "bathymetry_transect_id": str(row.bathymetry_transect_id),
                "segment_id": str(row.segment_id),
                "path": coords,
                "color": color,
                "origin_status": status,
            }
        )
    return pdk.Layer(
        "PathLayer",
        id="bathymetry-transects",
        data=records,
        pickable=True,
        get_path="path",
        get_color="color",
        get_width=2,
        width_units="pixels",
        width_min_pixels=1,
        opacity=0.45,
    )


def build_midpoint_layer(frame: gpd.GeoDataFrame) -> pdk.Layer | None:
    if frame.empty:
        return None
    records = [
        {
            "segment_id": str(row.segment_id),
            "position": [float(row.geometry.centroid.x), float(row.geometry.centroid.y)],
        }
        for _, row in frame.iterrows()
    ]
    return pdk.Layer(
        "ScatterplotLayer",
        id="segment-midpoints",
        data=records,
        pickable=True,
        get_position="position",
        get_fill_color=[245, 245, 245, 190],
        get_line_color=[45, 55, 65, 230],
        stroked=True,
        get_radius=22,
        radius_units="pixels",
        radius_min_pixels=3,
    )


def initial_view_state(frame: gpd.GeoDataFrame) -> pdk.ViewState:
    if frame.empty:
        return pdk.ViewState(latitude=39.82, longitude=2.73, zoom=9)
    left, bottom, right, top = frame.total_bounds
    longitude_span = max(float(right - left) / 360.0, 1e-9)

    def mercator_y(latitude: float) -> float:
        clipped = min(85.051129, max(-85.051129, latitude))
        radians = math.radians(clipped)
        return (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0

    latitude_span = max(abs(mercator_y(float(top)) - mercator_y(float(bottom))), 1e-9)
    usable_width = 1_200.0 * 0.72
    usable_height = 650.0 * 0.72
    zoom_x = math.log2(usable_width / (512.0 * longitude_span))
    zoom_y = math.log2(usable_height / (512.0 * latitude_span))
    zoom = min(16.0, max(1.0, min(zoom_x, zoom_y)))
    return pdk.ViewState(
        longitude=float((left + right) / 2),
        latitude=float((bottom + top) / 2),
        zoom=zoom,
        pitch=0,
        bearing=0,
    )


def build_deck(
    frame: gpd.GeoDataFrame,
    metric: MetricDefinition,
    scale_mode: ScaleMode,
    *,
    selected_segment_id: str | None = None,
    transects: gpd.GeoDataFrame | None = None,
    flags: set[str] | None = None,
    show_midpoints: bool = False,
    basemap: Literal["CARTO Light", "CARTO Dark", "Satellite"] = "CARTO Light",
    mapbox_token: str | None = None,
) -> tuple[pdk.Deck, ColorScale | None]:
    segment_result = build_segment_layer(
        frame, metric, scale_mode, selected_segment_id=selected_segment_id
    )
    layers: list[pdk.Layer] = [segment_result.layer]
    if transects is not None:
        transect_layer = build_transect_layer(transects)
        if transect_layer is not None:
            layers.insert(0, transect_layer)
    layers.extend(build_flag_layers(frame, flags or set()))
    if show_midpoints:
        midpoint_layer = build_midpoint_layer(frame)
        if midpoint_layer is not None:
            layers.append(midpoint_layer)
    provider = "mapbox" if basemap == "Satellite" and mapbox_token else "carto"
    style = (
        "satellite" if provider == "mapbox" else ("dark" if basemap == "CARTO Dark" else "light")
    )
    api_keys = {"mapbox": mapbox_token} if provider == "mapbox" and mapbox_token else None
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=initial_view_state(frame),
        map_provider=provider,
        map_style=style,
        api_keys=api_keys,
        tooltip={
            "html": (
                "<b>{segment_id}</b><br/>{metric_label}: {metric_value}<br/>"
                "Orientation: {orientation_status}<br/>Terrain: {terrain_quality}<br/>"
                "Bathymetry: {bathymetry_screening}<br/>First valid: {first_valid_distance}<br/>"
                "Source mismatch: {source_mismatch}"
            ),
            "style": {"backgroundColor": "#20252b", "color": "white"},
        },
    )
    return deck, segment_result.scale
