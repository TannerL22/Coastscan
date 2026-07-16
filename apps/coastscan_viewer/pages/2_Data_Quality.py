"""Data-quality distributions and weakness-focused map."""

import pandas as pd
import streamlit as st

from coastscan.exceptions import ViewerError
from coastscan.viewer.data import cache_fingerprint, load_viewer_data
from coastscan.viewer.layers import build_deck
from coastscan.viewer.metrics import metric_definition
from coastscan.viewer.runtime import requested_region
from coastscan.viewer.summaries import missing_value_counts

st.set_page_config(page_title="CoastScan — Data Quality", page_icon="🔎", layout="wide")
st.title("Data quality and analytical limitations")
st.warning(
    "This page foregrounds coverage, provenance and resolution weaknesses. Flags are not final "
    "site-level judgements."
)

region = st.session_state.get("viewer_region", requested_region())
try:
    data = load_viewer_data(region)
except ViewerError as exc:
    st.error(str(exc))
    st.stop()

segments = data.display_segments
first, second, third, fourth = st.columns(4)
first.metric("Segments", len(segments))
second.metric(
    "Ambiguous orientation",
    int((segments.orientation_status.astype(str) == "ambiguous").sum()),
)
third.metric(
    "Source mismatch",
    int(segments.orientation_source_mismatch_flag.fillna(False).astype(bool).sum()),
)
fourth.metric("Display CRS", "EPSG:4326")

distribution_fields = [
    "orientation_status",
    "terrain_quality_flag",
    "bathymetry_screening_class",
    "bathymetry_quality_flag",
]
columns = st.columns(2)
for index, field in enumerate(distribution_fields):
    if field not in segments:
        continue
    counts = segments[field].fillna("Missing").astype(str).value_counts().rename("segments")
    with columns[index % 2]:
        definition = metric_definition(field)
        st.subheader(definition.display_name if definition else field)
        st.bar_chart(counts)

numeric_fields = [
    "bathymetry_valid_transect_share",
    "bathymetry_first_valid_distance_p50_m",
    "global_fallback_source_share",
]
for field in numeric_fields:
    if field in segments and segments[field].notna().any():
        definition = metric_definition(field)
        st.subheader(definition.display_name if definition else field)
        st.bar_chart(pd.to_numeric(segments[field], errors="coerce").dropna())

important = [
    "land_relief_100m_p90_m",
    "slope_p90_deg",
    "terrain_valid_sample_share",
    "bathymetry_valid_transect_share",
    "bathymetry_first_valid_distance_p50_m",
    "depth_250m_p50_m",
    "depth_500m_p50_m",
    "gradient_250_1000m_p50",
    "global_fallback_source_share",
]
st.subheader("Missing important fields")
st.dataframe(missing_value_counts(segments, important), hide_index=True, width="stretch")

quality_metric_fields = [
    field
    for field in (
        "orientation_status",
        "terrain_quality_flag",
        "bathymetry_screening_class",
        "bathymetry_valid_transect_share",
        "bathymetry_first_valid_distance_p50_m",
    )
    if field in segments
]
selected_field = st.selectbox(
    "Quality map metric",
    quality_metric_fields,
    format_func=lambda field: metric_definition(field).display_name,  # type: ignore[union-attr]
)
focus = st.multiselect(
    "Weakness overlays",
    [
        "ambiguous",
        "source_mismatch",
        "large_coastal_gap",
        "missing_terrain",
        "missing_bathymetry",
        "global_fallback",
    ],
    default=["ambiguous", "source_mismatch", "large_coastal_gap", "missing_bathymetry"],
)
deck, _ = build_deck(
    segments,
    metric_definition(selected_field),  # type: ignore[arg-type]
    "robust",
    flags=set(focus),
)
st.pydeck_chart(deck, width="stretch", height=600)

source_columns = st.columns(3)
source_columns[0].metric(
    "Native bathymetry resolution",
    (
        f"{segments.bathymetry_native_resolution_m.dropna().median():.0f} m"
        if "bathymetry_native_resolution_m" in segments
        and segments.bathymetry_native_resolution_m.notna().any()
        else "Not available"
    ),
)
source_columns[1].metric(
    "Vertical datum",
    (
        str(segments.bathymetry_vertical_datum.dropna().mode().iloc[0])
        if "bathymetry_vertical_datum" in segments
        and segments.bathymetry_vertical_datum.notna().any()
        else "Not available"
    ),
)
source_columns[2].metric("Mode", data.mode.replace("_", " ").title())
if "bathymetry_source_id" in segments:
    st.write(
        "**Phase 2 source references:** "
        + ", ".join(sorted(segments.bathymetry_source_id.dropna().astype(str).unique()))
    )
st.write("**Viewer input provenance:**")
st.json(cache_fingerprint(data))
if data.manifests:
    st.subheader("Latest upstream manifests")
    for stage, manifest in data.manifests.items():
        st.json(
            {
                "stage": stage,
                "run_id": manifest.get("run_id"),
                "git_commit": manifest.get("git_commit"),
                "status": manifest.get("status"),
                "source_release": manifest.get("source_release"),
                "input_files": manifest.get("input_files"),
                "bathymetry_sources": manifest.get("bathymetry_sources"),
            }
        )

st.error(
    "Regional bathymetry cannot resolve individual submerged ledges or rocks. EMODnet is not "
    "navigation data, and coarse cells may contain fallback or interpolated information."
)
