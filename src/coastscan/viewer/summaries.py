"""Small descriptive summaries for viewer pages."""

from typing import Any

import geopandas as gpd
import pandas as pd


def _positive_count(frame: pd.DataFrame, field: str) -> int:
    if field not in frame:
        return 0
    values = pd.to_numeric(frame[field], errors="coerce")
    return int((values.fillna(0) > 0).sum())


def summary_counts(all_segments: gpd.GeoDataFrame, visible: gpd.GeoDataFrame) -> dict[str, Any]:
    orientation = all_segments.get(
        "orientation_status", pd.Series(index=all_segments.index, dtype=object)
    ).astype(str)
    mismatch = all_segments.get(
        "orientation_source_mismatch_flag", pd.Series(False, index=all_segments.index)
    ).fillna(False)
    screening = all_segments.get(
        "bathymetry_screening_class", pd.Series(index=all_segments.index, dtype=object)
    ).dropna()
    return {
        "total_segments": len(all_segments),
        "visible_segments": len(visible),
        "resolved_orientation_segments": int(
            orientation.isin(["resolved", "resolved_fallback"]).sum()
        ),
        "ambiguous_orientation_segments": int((orientation == "ambiguous").sum()),
        "terrain_feature_segments": _positive_count(all_segments, "terrain_valid_sample_share"),
        "bathymetry_feature_segments": _positive_count(
            all_segments, "bathymetry_valid_transect_share"
        ),
        "source_mismatch_segments": int(mismatch.astype(bool).sum()),
        "bathymetry_screening_distribution": {
            str(key): int(value) for key, value in screening.value_counts().items()
        },
    }


def missing_value_counts(frame: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    rows = [
        {"field": field, "missing": int(frame[field].isna().sum()), "total": len(frame)}
        for field in fields
        if field in frame
    ]
    return pd.DataFrame(rows)
