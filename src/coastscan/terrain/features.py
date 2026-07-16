"""Aggregate inland samples into interpretable segment morphology features."""

import numpy as np
import pandas as pd


def _percentile(values: list[float] | pd.Series, percentile: float) -> float:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    return float(np.percentile(array, percentile)) if array.size else float("nan")


def _distance_values(samples: pd.DataFrame, distance: float, tolerance: float) -> pd.Series:
    return samples.loc[(samples.sample_distance_m - distance).abs() <= tolerance, "elevation_m"]


def calculate_terrain_features(
    segments: pd.DataFrame,
    transects: pd.DataFrame,
    samples: pd.DataFrame,
    relief_distances_m: list[float],
    sample_spacing_m: float,
    steep_threshold_deg: float,
    minimum_valid_sample_share: float,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    tolerance = sample_spacing_m / 2 + 1e-6
    inland = transects.loc[transects.direction == "inland"]
    for _, segment in segments.iterrows():
        segment_transects = inland.loc[inland.segment_id == segment.segment_id]
        segment_samples = samples.loc[samples.segment_id == segment.segment_id]
        record: dict[str, object] = {"segment_id": segment.segment_id}
        total_samples = len(segment_samples)
        valid_elevation = (
            segment_samples.elevation_m.notna() if total_samples else pd.Series(dtype=bool)
        )
        valid_count = int(valid_elevation.sum())
        valid_share = valid_count / total_samples if total_samples else 0.0
        coast = segment_samples.loc[segment_samples.sample_distance_m <= tolerance, "elevation_m"]
        record["elevation_coast_p50_m"] = _percentile(coast, 50)
        for distance in relief_distances_m:
            label = f"{distance:g}m"
            elevation_values = _distance_values(segment_samples, distance, tolerance)
            record[f"elevation_{label}_p50_m"] = _percentile(elevation_values, 50)
            reliefs: list[float] = []
            for _, transect in segment_transects.iterrows():
                transect_samples = segment_samples.loc[
                    segment_samples.transect_id == transect.transect_id
                ]
                origin = _distance_values(transect_samples, 0, tolerance)
                at_distance = _distance_values(transect_samples, distance, tolerance)
                if origin.notna().any() and at_distance.notna().any():
                    reliefs.append(float(at_distance.dropna().iloc[0] - origin.dropna().iloc[0]))
            record[f"land_relief_{label}_p50_m"] = _percentile(reliefs, 50)
            record[f"land_relief_{label}_p90_m"] = _percentile(reliefs, 90)
        valid_slopes = segment_samples.slope_deg.dropna()
        record["slope_p50_deg"] = _percentile(valid_slopes, 50)
        record["slope_p90_deg"] = _percentile(valid_slopes, 90)
        record["slope_max_deg"] = (
            float(valid_slopes.max()) if not valid_slopes.empty else float("nan")
        )
        record["steep_sample_share"] = (
            float((valid_slopes >= steep_threshold_deg).mean())
            if not valid_slopes.empty
            else float("nan")
        )
        first_steep: list[float] = []
        max_slope_distance: list[float] = []
        valid_transects = 0
        nearshore_steep = 0
        for _, transect in segment_transects.iterrows():
            group = segment_samples.loc[segment_samples.transect_id == transect.transect_id]
            valid = group.loc[group.elevation_m.notna() & group.slope_deg.notna()]
            if valid.empty:
                continue
            valid_transects += 1
            steep = valid.loc[valid.slope_deg >= steep_threshold_deg]
            if not steep.empty:
                first_steep.append(float(steep.sample_distance_m.min()))
                if (steep.sample_distance_m <= 25).any():
                    nearshore_steep += 1
            max_slope_distance.append(
                float(valid.loc[valid.slope_deg.idxmax(), "sample_distance_m"])
            )
        record["distance_to_first_steep_sample_p50_m"] = _percentile(first_steep, 50)
        record["distance_to_max_slope_p50_m"] = _percentile(max_slope_distance, 50)
        record["steep_nearshore_transect_share"] = (
            nearshore_steep / valid_transects if valid_transects else float("nan")
        )
        record["roughness_p50"] = (
            _percentile(segment_samples.roughness, 50) if total_samples else float("nan")
        )
        record["roughness_p90"] = (
            _percentile(segment_samples.roughness, 90) if total_samples else float("nan")
        )
        record["terrain_transect_count"] = len(segment_transects)
        record["terrain_valid_transect_count"] = valid_transects
        record["terrain_valid_transect_share"] = (
            valid_transects / len(segment_transects) if len(segment_transects) else 0.0
        )
        record["terrain_sample_count"] = total_samples
        record["terrain_valid_sample_count"] = valid_count
        record["terrain_valid_sample_share"] = valid_share
        if segment.orientation_status not in {"resolved", "resolved_fallback"}:
            quality = "orientation_unresolved"
        elif total_samples == 0 or valid_count == 0:
            quality = "outside_dem"
        elif valid_share >= minimum_valid_sample_share:
            quality = "good"
        elif valid_share > 0:
            quality = "partial" if valid_share >= minimum_valid_sample_share / 2 else "insufficient"
        else:
            quality = "insufficient"
        record["terrain_quality_flag"] = quality
        records.append(record)
    return pd.DataFrame(records)
