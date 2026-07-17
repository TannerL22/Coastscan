"""CoastScan local interactive exploration viewer."""

import os

import numpy as np
import pandas as pd
import streamlit as st

from coastscan.exceptions import ViewerError
from coastscan.viewer.data import load_display_transects, load_viewer_data
from coastscan.viewer.filters import apply_filters, reset_filter_state
from coastscan.viewer.formatting import (
    analytical_csv_table,
    deterministic_interpretation,
    format_field,
    format_value,
    screening_reasons,
    table_columns,
)
from coastscan.viewer.layers import (
    build_deck,
    categorical_color,
    continuous_color,
)
from coastscan.viewer.metrics import available_metrics, metric_definition
from coastscan.viewer.models import ViewerFilters
from coastscan.viewer.runtime import available_regions, requested_region
from coastscan.viewer.selection import (
    preserve_selection,
    segment_id_from_pydeck_event,
    segment_id_from_table_event,
)
from coastscan.viewer.summaries import summary_counts

st.set_page_config(
    page_title="CoastScan — Mallorca Northwest Exploration Viewer",
    page_icon="🌊",
    layout="wide",
)


def _numeric_extent(frame: pd.DataFrame, field: str) -> tuple[float, float] | None:
    if field not in frame:
        return None
    values = pd.to_numeric(frame[field], errors="coerce")
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    return float(values.min()), float(values.max())


def _range_control(
    label: str,
    frame: pd.DataFrame,
    field: str,
    key: str,
    *,
    value_format: str = "%.2f",
) -> tuple[float | None, float | None]:
    extent = _numeric_extent(frame, field)
    if extent is None:
        return (None, None)
    minimum, maximum = extent
    if np.isclose(minimum, maximum):
        st.caption(f"{label}: constant at {minimum:.2f}")
        return (None, None)
    selected = st.slider(
        label,
        minimum,
        maximum,
        (minimum, maximum),
        format=value_format,
        key=key,
    )
    return (None, None) if selected == (minimum, maximum) else selected


def _single_limit(
    label: str,
    frame: pd.DataFrame,
    field: str,
    key: str,
    *,
    lower: bool,
) -> float | None:
    extent = _numeric_extent(frame, field)
    if extent is None or np.isclose(*extent):
        return None
    minimum, maximum = extent
    default = minimum if lower else maximum
    selected = st.slider(label, minimum, maximum, default, key=key)
    return None if np.isclose(selected, default) else float(selected)


def _metric_cards(counts: dict[str, object]) -> None:
    columns = st.columns(7)
    labels = [
        ("Total segments", "total_segments"),
        ("Visible", "visible_segments"),
        ("Resolved orientation", "resolved_orientation_segments"),
        ("Ambiguous", "ambiguous_orientation_segments"),
        ("Terrain features", "terrain_feature_segments"),
        ("Bathymetry features", "bathymetry_feature_segments"),
        ("Source mismatch", "source_mismatch_segments"),
    ]
    for column, (label, key) in zip(columns, labels, strict=True):
        column.metric(label, counts[key])
    screening = counts["bathymetry_screening_distribution"]
    if isinstance(screening, dict) and screening:
        st.caption(
            "Bathymetry screening: "
            + " · ".join(
                f"{str(key).replace('_', ' ')}: {value}" for key, value in screening.items()
            )
        )


def _display_metrics(row: pd.Series, fields: list[str], columns: int = 3) -> None:
    available = [field for field in fields if field in row.index]
    for start in range(0, len(available), columns):
        display_columns = st.columns(columns)
        for column, field in zip(display_columns, available[start : start + columns], strict=False):
            definition = metric_definition(field)
            label = definition.display_name if definition else field.replace("_", " ").title()
            column.metric(label, format_value(row.get(field), definition))


def _color_chip(color: list[int], label: str) -> str:
    red, green, blue, alpha = color
    return (
        f'<span style="display:inline-block;width:0.9rem;height:0.9rem;border-radius:0.2rem;'
        f'background:rgba({red},{green},{blue},{alpha / 255:.2f});margin-right:0.35rem"></span>'
        f"{label}"
    )


def _render_legend(
    frame: pd.DataFrame,
    field: str,
    scale: object,
) -> None:
    definition = metric_definition(field)
    if definition is None:
        return
    if definition.kind == "continuous" and scale is not None:
        minimum = getattr(scale, "minimum", None)
        maximum = getattr(scale, "maximum", None)
        if minimum is None or maximum is None:
            return
        midpoint = (minimum + maximum) / 2
        items = [
            _color_chip(
                continuous_color(
                    value,
                    scale,
                    diverging=definition.recommended_scale == "diverging",
                ),
                f"{value:.3g} {definition.unit}".strip(),
            )
            for value in (minimum, midpoint, maximum)
        ]
    else:
        values = sorted(frame[field].dropna().astype(str).unique())
        items = [
            _color_chip(categorical_color(field, value), value.replace("_", " ").title())
            for value in values
        ]
    if items:
        st.markdown(" &nbsp; ".join(items), unsafe_allow_html=True)


def _selected_segment_panel(row: pd.Series, coastline_source_id: str | None) -> None:
    st.subheader("Selected segment")
    identity, terrain, bathymetry = st.tabs(
        ["Identity & provenance", "Terrain profile", "Bathymetry profile"]
    )
    with identity:
        st.code(str(row.segment_id), language=None)
        first, second, third, fourth = st.columns(4)
        first.metric("Region", row.get("region_id", "Not recorded"))
        second.metric("Coastline part", row.get("coastline_part_id", "Not recorded"))
        second.caption("Stable Phase 1 coastline component")
        third.metric("Segment length", f"{float(row.get('segment_length_m', 0)):.1f} m")
        fourth.metric("Orientation", str(row.get("orientation_status", "Not recorded")))
        st.write(
            "**Coastline source:** "
            f"`{coastline_source_id or 'See Phase 1 manifest'}` · coastline version "
            f"`{row.get('coastline_version', 'not recorded')}`"
        )
        st.write(
            "**Source-mismatch flag:** "
            + ("Yes" if bool(row.get("orientation_source_mismatch_flag", False)) else "No")
        )
    with terrain:
        _display_metrics(
            row,
            [
                "land_relief_25m_p50_m",
                "land_relief_50m_p50_m",
                "land_relief_100m_p50_m",
                "slope_p50_deg",
                "slope_p90_deg",
                "slope_max_deg",
                "steep_sample_share",
                "steep_nearshore_transect_share",
                "distance_to_first_steep_sample_p50_m",
                "roughness_p90",
                "terrain_valid_sample_share",
                "terrain_quality_flag",
            ],
        )
        st.caption("Terrain metrics describe land only; they provide no underwater evidence.")
    with bathymetry:
        if "bathymetry_source_id" not in row.index:
            st.info("Terrain-only mode: Phase 2 output is not loaded for this region.")
        else:
            source_columns = st.columns(4)
            source_columns[0].metric("Source", row.get("bathymetry_source_id", "Not recorded"))
            source_columns[1].metric(
                "Native resolution",
                format_field(row, "bathymetry_native_resolution_m"),
            )
            source_columns[2].metric("Vertical datum", row.get("bathymetry_vertical_datum", ""))
            source_columns[3].metric(
                "Screening class", format_field(row, "bathymetry_screening_class")
            )
            _display_metrics(
                row,
                [
                    "bathymetry_valid_transect_share",
                    "bathymetry_first_valid_distance_p50_m",
                    "bathymetry_first_valid_distance_p90_m",
                    "depth_100m_p50_m",
                    "depth_250m_p50_m",
                    "depth_500m_p50_m",
                    "depth_1000m_p50_m",
                    "gradient_100_500m_p50",
                    "gradient_250_1000m_p50",
                    "distance_to_5m_depth_p50_m",
                    "distance_to_10m_depth_p50_m",
                    "distance_to_20m_depth_p50_m",
                    "distance_to_30m_depth_p50_m",
                    "global_fallback_source_share",
                    "interpolated_cell_share",
                    "extrapolated_cell_share",
                    "bathymetry_quality_flag",
                ],
            )
            reasons = screening_reasons(row.get("bathymetry_screening_reasons"))
            if reasons:
                st.write("**Screening reasons:** " + ", ".join(reasons))
            st.caption(
                "Regional bathymetry is not a measurement beneath an individual cliff and does "
                "not resolve submerged obstacles."
            )
    st.info(deterministic_interpretation(row), icon="ℹ️")


st.title("CoastScan")
st.subheader("Mallorca Northwest Exploration Viewer")
st.warning(
    "CoastScan supports regional desktop exploration. Terrain and regional bathymetry do not "
    "establish site-level water depth, underwater clearance or safety."
)

initial_region = requested_region()
regions = available_regions()
if initial_region not in regions:
    regions.insert(0, initial_region)
if "viewer_region" not in st.session_state:
    st.session_state.viewer_region = initial_region
region = st.sidebar.selectbox("Region", regions, key="viewer_region")
if st.session_state.get("_loaded_region") != region:
    reset_filter_state(st.session_state)
    st.session_state.selected_segment_id = None
    st.session_state.segment_picker = None
    st.session_state._loaded_region = region

try:
    data = load_viewer_data(region)
except ViewerError as exc:
    st.error(str(exc))
    st.stop()

if not data.has_bathymetry:
    st.info(
        "Terrain-only mode. Build Phase 2 to enable regional bathymetry controls:\n\n"
        f"`uv run coastscan build-bathymetry --region {region} --write-samples`"
    )

metric_options = available_metrics(
    data.display_segments.columns, include_bathymetry=data.has_bathymetry
)
if not metric_options:
    st.error("No registered display metrics are present in the processed segment table.")
    st.stop()
metric_by_field = {metric.field_name: metric for metric in metric_options}
if st.session_state.get("viewer_metric") not in metric_by_field:
    st.session_state.viewer_metric = metric_options[0].field_name
selected_metric_field = st.sidebar.selectbox(
    "Display metric",
    list(metric_by_field),
    format_func=lambda field: (
        f"{metric_by_field[field].category.title()} · {metric_by_field[field].display_name}"
    ),
    key="viewer_metric",
)
metric = metric_by_field[selected_metric_field]
scale_mode = st.sidebar.radio(
    "Continuous colour range",
    ["robust", "full"],
    format_func=lambda value: (
        "Robust 5th–95th percentile" if value == "robust" else "Full observed range"
    ),
    horizontal=True,
    key="viewer_scale_mode",
)
st.sidebar.caption(metric.description)
st.sidebar.caption(metric.safety_interpretation)

if st.sidebar.button("Reset filters", use_container_width=True):
    reset_filter_state(st.session_state)
    st.rerun()

with st.sidebar.expander("Geometry and availability", expanded=True):
    orientation_values = sorted(
        data.display_segments.orientation_status.dropna().astype(str).unique()
    )
    orientation = st.multiselect("Orientation status", orientation_values, key="filter_orientation")
    terrain_availability = st.selectbox(
        "Terrain availability",
        ["all", "available", "missing"],
        key="filter_terrain_availability",
    )
    bathymetry_availability = (
        st.selectbox(
            "Bathymetry availability",
            ["all", "available", "missing"],
            key="filter_bathymetry_availability",
        )
        if data.has_bathymetry
        else "all"
    )
    mismatch_choice = st.selectbox(
        "Source mismatch",
        ["all", "flagged", "not flagged"],
        key="filter_source_mismatch",
    )
    search = st.text_input("Segment ID contains", key="filter_search")

with st.sidebar.expander("Terrain filters"):
    relief_range = _range_control(
        "Relief p90 within 100 m",
        data.display_segments,
        "land_relief_100m_p90_m",
        "filter_relief_range",
    )
    slope_range = _range_control(
        "Slope p90",
        data.display_segments,
        "slope_p90_deg",
        "filter_slope_range",
    )
    minimum_steep = st.slider(
        "Minimum steep near-coast share", 0.0, 1.0, 0.0, key="filter_min_steep_share"
    )
    minimum_terrain = st.slider(
        "Minimum terrain valid share", 0.0, 1.0, 0.0, key="filter_min_terrain_valid"
    )

screening: list[str] = []
minimum_bathy = None
maximum_first_valid = None
depth_field = None
depth_range: tuple[float | None, float | None] = (None, None)
gradient_field = None
gradient_range: tuple[float | None, float | None] = (None, None)
maximum_fallback = None
if data.has_bathymetry:
    with st.sidebar.expander("Bathymetry filters"):
        screening_values = sorted(
            data.display_segments.bathymetry_screening_class.dropna().astype(str).unique()
        )
        screening = st.multiselect("Screening class", screening_values, key="filter_screening")
        minimum_bathy_value = st.slider(
            "Minimum valid-transect share",
            0.0,
            1.0,
            0.0,
            key="filter_min_bathymetry_valid",
        )
        minimum_bathy = minimum_bathy_value if minimum_bathy_value > 0 else None
        maximum_first_valid = _single_limit(
            "Maximum median first-valid distance",
            data.display_segments,
            "bathymetry_first_valid_distance_p50_m",
            "filter_max_first_valid",
            lower=False,
        )
        depth_fields = [
            field
            for field in (
                "depth_100m_p50_m",
                "depth_250m_p50_m",
                "depth_500m_p50_m",
                "depth_1000m_p50_m",
            )
            if field in data.display_segments
        ]
        if depth_fields:
            depth_field = st.selectbox(
                "Depth proxy filter",
                [None, *depth_fields],
                format_func=lambda field: (
                    "None" if field is None else metric_definition(field).display_name
                ),  # type: ignore[union-attr]
                key="filter_depth_field",
            )
            if depth_field:
                depth_range = _range_control(
                    "Depth proxy range",
                    data.display_segments,
                    depth_field,
                    "filter_depth_range",
                )
        gradient_fields = [
            field
            for field in ("gradient_100_500m_p50", "gradient_250_1000m_p50")
            if field in data.display_segments and data.display_segments[field].notna().any()
        ]
        if gradient_fields:
            gradient_field = st.selectbox(
                "Gradient filter",
                [None, *gradient_fields],
                format_func=lambda field: (
                    "None" if field is None else metric_definition(field).display_name
                ),  # type: ignore[union-attr]
                key="filter_gradient_field",
            )
            if gradient_field:
                gradient_range = _range_control(
                    "Gradient range",
                    data.display_segments,
                    gradient_field,
                    "filter_gradient_range",
                    value_format="%.4f",
                )
        maximum_fallback = _single_limit(
            "Maximum global-fallback share",
            data.display_segments,
            "global_fallback_source_share",
            "filter_max_fallback",
            lower=False,
        )

filters = ViewerFilters(
    orientation_statuses=frozenset(orientation) if orientation else None,
    terrain_availability=terrain_availability,
    bathymetry_availability=bathymetry_availability,
    source_mismatch=(
        True
        if mismatch_choice == "flagged"
        else False
        if mismatch_choice == "not flagged"
        else None
    ),
    relief_100m_range=relief_range,
    slope_p90_range=slope_range,
    minimum_steep_nearshore_share=minimum_steep if minimum_steep > 0 else None,
    minimum_terrain_valid_share=minimum_terrain if minimum_terrain > 0 else None,
    bathymetry_screening_classes=frozenset(screening) if screening else None,
    minimum_bathymetry_valid_share=minimum_bathy,
    maximum_first_valid_distance_m=maximum_first_valid,
    depth_field=depth_field,
    depth_range=depth_range,
    gradient_field=gradient_field,
    gradient_range=gradient_range,
    maximum_global_fallback_share=maximum_fallback,
    segment_search=search,
)
visible = apply_filters(data.display_segments, filters)
counts = summary_counts(data.display_segments, visible)
_metric_cards(counts)

if visible.empty:
    st.warning("No coastline segments meet the active filters. Reset or broaden the filters.")
    st.stop()

visible_ids = set(visible.segment_id.astype(str))
selected_segment_id = preserve_selection(st.session_state.get("selected_segment_id"), visible_ids)
pending = st.session_state.pop("_pending_segment_picker", None)
if pending in visible_ids:
    st.session_state.segment_picker = pending
    selected_segment_id = pending
if st.session_state.get("segment_picker") not in {None, *visible_ids}:
    st.session_state.segment_picker = None
picker = st.selectbox(
    "Inspect a visible segment",
    [None, *sorted(visible_ids)],
    format_func=lambda value: "Select by segment ID…" if value is None else value,
    key="segment_picker",
)
if picker is not None:
    selected_segment_id = picker
st.session_state.selected_segment_id = selected_segment_id

with st.sidebar.expander("Map layers", expanded=True):
    flag_labels = {
        "ambiguous": "Ambiguous orientation",
        "source_mismatch": "Source mismatch",
        "large_coastal_gap": "Large coastal gap",
        "missing_bathymetry": "Missing bathymetry",
        "missing_terrain": "Missing terrain",
        "global_fallback": "Fallback/background bathymetry",
    }
    enabled_labels = st.multiselect(
        "Flag overlays",
        list(flag_labels.values()),
        key="viewer_flag_layers",
    )
    enabled_flags = {key for key, label in flag_labels.items() if label in enabled_labels}
    show_midpoints = st.checkbox("Segment midpoints", key="viewer_midpoints")
    show_transects = st.checkbox(
        "Bathymetry transects",
        disabled=not data.has_bathymetry,
        key="viewer_transects",
    )
    transect_mode = st.radio(
        "Transect scope",
        ["Selected segment only", "All visible segments"],
        disabled=not show_transects,
        key="viewer_transect_mode",
    )
    mapbox_token = os.environ.get("MAPBOX_API_KEY")
    basemaps = ["CARTO Light", "CARTO Dark"] + (["Satellite"] if mapbox_token else [])
    basemap = st.selectbox("Basemap", basemaps, key="viewer_basemap")
    if not mapbox_token:
        st.caption("Satellite is unavailable without an optional user-supplied Mapbox token.")

transects = None
if show_transects:
    if transect_mode == "Selected segment only":
        transect_ids = {selected_segment_id} if selected_segment_id else set()
    else:
        transect_ids = visible_ids
    transects = load_display_transects(data, transect_ids)

deck, scale = build_deck(
    visible,
    metric,
    scale_mode,
    selected_segment_id=selected_segment_id,
    transects=transects,
    flags=enabled_flags,
    show_midpoints=show_midpoints,
    basemap=basemap,
    mapbox_token=mapbox_token,
)
if scale is not None:
    if scale.minimum is None:
        st.caption(
            f"{metric.display_name}: no valid values among visible segments; missing values "
            "are grey."
        )
    else:
        unit = f" {metric.unit}" if metric.unit else ""
        constant = " · constant field" if scale.constant else ""
        st.caption(
            f"Active {scale.mode} scale: {scale.minimum:.3g}{unit} to {scale.maximum:.3g}{unit}"
            f" · {scale.valid_count} valid values{constant} · missing values are grey. "
            "Colour indicates magnitude, not desirability."
        )
else:
    st.caption("Distinct colours represent categories, not desirability or site-level judgement.")
_render_legend(visible, selected_metric_field, scale)

map_event = st.pydeck_chart(
    deck,
    # Version the stateful chart key when its deck.gl contract changes so a
    # browser cannot retain the old malformed camera/layer state.
    key="coastscan_map_phase251_literal_units",
    on_select="rerun",
    selection_mode="single-object",
    width="stretch",
    height=650,
)
clicked = segment_id_from_pydeck_event(map_event)
if clicked in visible_ids and clicked != selected_segment_id:
    st.session_state.selected_segment_id = clicked
    st.session_state._pending_segment_picker = clicked
    st.rerun()

with st.expander("Visible segment table and CSV export", expanded=False):
    table_fields = table_columns(visible, selected_metric_field)
    table = pd.DataFrame(visible[table_fields]).reset_index(drop=True)
    table_event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=320,
        key="visible_segment_table",
        on_select="rerun",
        selection_mode="single-row",
    )
    table_selected = segment_id_from_table_event(table_event, table)
    if table_selected in visible_ids and table_selected != selected_segment_id:
        st.session_state.selected_segment_id = table_selected
        st.session_state._pending_segment_picker = table_selected
        st.rerun()
    export = analytical_csv_table(visible[table_fields])
    st.download_button(
        "Download filtered analytical attributes (CSV)",
        export.to_csv(index=False).encode("utf-8"),
        file_name=f"coastscan_{region}_visible_segments.csv",
        mime="text/csv",
        on_click="ignore",
    )

if selected_segment_id:
    selected_row = visible.loc[visible.segment_id.astype(str) == selected_segment_id].iloc[0]
    _selected_segment_panel(selected_row, data.coastline_source_id)
else:
    st.info(
        "Select a segment on the map, by searchable ID, or from the visible table to inspect it."
    )

st.divider()
st.caption(
    "Terrain is terrestrial only. EMODnet regional bathymetry is not navigation data. Coarse cells "
    "can contain fallback or interpolated information; submerged obstacles remain unresolved and "
    "conditions change over time. Exact sites require legal, environmental and physical assessment."
)
