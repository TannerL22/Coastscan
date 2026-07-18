"""Composable, non-mutating filters for display-only segment data."""

from collections.abc import MutableMapping
from typing import Any

import geopandas as gpd
import pandas as pd

from coastscan.viewer.models import Availability, ViewerFilters


def _availability_mask(
    frame: pd.DataFrame,
    availability: Availability,
    field: str,
) -> pd.Series:
    if availability == "all":
        return pd.Series(True, index=frame.index)
    if field not in frame:
        available = pd.Series(False, index=frame.index)
    else:
        values = pd.to_numeric(frame[field], errors="coerce")
        available = values.notna() & (values > 0)
    return available if availability == "available" else ~available


def _range_mask(
    frame: pd.DataFrame,
    field: str,
    bounds: tuple[float | None, float | None],
) -> pd.Series:
    lower, upper = bounds
    if lower is None and upper is None:
        return pd.Series(True, index=frame.index)
    if field not in frame:
        return pd.Series(False, index=frame.index)
    values = pd.to_numeric(frame[field], errors="coerce")
    mask = values.notna()
    if lower is not None:
        mask &= values >= lower
    if upper is not None:
        mask &= values <= upper
    return mask


def _minimum_mask(frame: pd.DataFrame, field: str, value: float | None) -> pd.Series:
    return _range_mask(frame, field, (value, None))


def _maximum_mask(frame: pd.DataFrame, field: str, value: float | None) -> pd.Series:
    return _range_mask(frame, field, (None, value))


def apply_filters(frame: gpd.GeoDataFrame, filters: ViewerFilters) -> gpd.GeoDataFrame:
    """Return a filtered copy without changing source or cached frames."""
    mask = pd.Series(True, index=frame.index)
    if filters.orientation_statuses:
        if "orientation_status" not in frame:
            mask &= False
        else:
            mask &= frame.orientation_status.astype(str).isin(filters.orientation_statuses)
    mask &= _availability_mask(frame, filters.terrain_availability, "terrain_valid_sample_share")
    mask &= _availability_mask(
        frame, filters.bathymetry_availability, "bathymetry_valid_transect_share"
    )
    if filters.source_mismatch is not None:
        if "orientation_source_mismatch_flag" not in frame:
            mask &= not filters.source_mismatch
        else:
            mismatch = frame.orientation_source_mismatch_flag.fillna(False).astype(bool)
            mask &= mismatch == filters.source_mismatch
    mask &= _range_mask(frame, "land_relief_100m_p90_m", filters.relief_100m_range)
    mask &= _range_mask(frame, "slope_p90_deg", filters.slope_p90_range)
    mask &= _minimum_mask(
        frame, "steep_nearshore_transect_share", filters.minimum_steep_nearshore_share
    )
    mask &= _minimum_mask(frame, "terrain_valid_sample_share", filters.minimum_terrain_valid_share)
    if filters.bathymetry_screening_classes:
        if "bathymetry_screening_class" not in frame:
            mask &= False
        else:
            mask &= frame.bathymetry_screening_class.astype(str).isin(
                filters.bathymetry_screening_classes
            )
    mask &= _minimum_mask(
        frame, "bathymetry_valid_transect_share", filters.minimum_bathymetry_valid_share
    )
    mask &= _maximum_mask(
        frame,
        "bathymetry_first_valid_distance_p50_m",
        filters.maximum_first_valid_distance_m,
    )
    if filters.depth_field:
        mask &= _range_mask(frame, filters.depth_field, filters.depth_range)
    if filters.gradient_field:
        mask &= _range_mask(frame, filters.gradient_field, filters.gradient_range)
    mask &= _maximum_mask(
        frame, "global_fallback_source_share", filters.maximum_global_fallback_share
    )
    mask &= _minimum_mask(frame, "valid_scene_count", filters.minimum_valid_scenes)
    mask &= _minimum_mask(frame, "valid_year_count", filters.minimum_valid_years)
    mask &= _minimum_mask(frame, "valid_month_count", filters.minimum_valid_months)
    mask &= _minimum_mask(frame, "clarity_percentile_p50", filters.minimum_clarity_percentile)
    mask &= _minimum_mask(frame, "clear_water_observation_share", filters.minimum_clear_water_share)
    mask &= _minimum_mask(frame, "clarity_persistence", filters.minimum_clarity_persistence)
    mask &= _maximum_mask(frame, "glint_excluded_share", filters.maximum_glint_exclusion)
    mask &= _maximum_mask(frame, "shadow_excluded_share", filters.maximum_shadow_exclusion)
    if filters.clarity_confidences:
        mask &= (
            frame.clarity_data_confidence.astype(str).isin(filters.clarity_confidences)
            if "clarity_data_confidence" in frame
            else False
        )
    if filters.clarity_qualities:
        mask &= (
            frame.clarity_quality_flag.astype(str).isin(filters.clarity_qualities)
            if "clarity_quality_flag" in frame
            else False
        )
    search = filters.segment_search.strip().casefold()
    if search:
        mask &= frame.segment_id.astype(str).str.casefold().str.contains(search, regex=False)
    return frame.loc[mask].copy()


def reset_filter_state(state: MutableMapping[str, Any]) -> None:
    for key in list(state):
        if str(key).startswith("filter_"):
            del state[key]
