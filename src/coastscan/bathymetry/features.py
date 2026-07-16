"""Transparent segment-level regional bathymetry proxy features."""

import json
from collections.abc import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd

from coastscan.bathymetry.adapters import resolution_class
from coastscan.models.region import BathymetryConfig, BathymetryInput


def _percentile(values: Iterable[float], percentile: float) -> float:
    array = np.asarray(list(values), dtype="float64")
    array = array[np.isfinite(array)]
    return float(np.percentile(array, percentile)) if len(array) else float("nan")


def _share(mask: pd.Series) -> float:
    return float(mask.mean()) if len(mask) else float("nan")


def _name(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", "p")


def _target_rows(samples: pd.DataFrame, distance: float) -> pd.DataFrame:
    return samples[np.isclose(samples["distance_from_coast_m"], distance)]


def _screening_class(
    valid_share: float,
    settings: BathymetryConfig,
    source: BathymetryInput,
) -> tuple[str, list[str]]:
    reasons = [
        f"native_resolution_m={source.native_resolution_m:g}",
        f"valid_transect_share={valid_share:.3f}",
        "regional_proxy_not_site_safety_evidence",
    ]
    if not np.isfinite(valid_share) or valid_share < settings.minimum_valid_transect_share:
        reasons.append("below_minimum_valid_transect_share")
        return "insufficient", reasons
    base = "regional_screening"
    rank = {
        "insufficient": 0,
        "background_only": 1,
        "regional_screening": 2,
        "coastal_context": 3,
        "local_morphology_candidate": 4,
    }
    if rank[source.screening_class_ceiling] < rank[base]:
        base = source.screening_class_ceiling
        reasons.append(f"source_assessment_ceiling={source.screening_class_ceiling}")
    return base, reasons


def calculate_bathymetry_features(
    segments: gpd.GeoDataFrame,
    transects: gpd.GeoDataFrame,
    samples: pd.DataFrame,
    settings: BathymetryConfig,
    source: BathymetryInput,
) -> pd.DataFrame:
    """Aggregate transect proxies while retaining explicit validity and resolution limits."""
    rows: list[dict[str, object]] = []
    for segment in segments.sort_values("segment_id").itertuples():
        segment_transects = transects[transects.segment_id == segment.segment_id]
        segment_samples = samples[samples.segment_id == segment.segment_id]
        origins = segment_transects.first_valid_depth_distance_m.dropna()
        valid_transects = segment_transects[
            segment_transects.bathymetry_origin_status != "no_valid_bathymetry"
        ]
        count = len(segment_transects)
        valid_count = len(valid_transects)
        valid_share = valid_count / count if count else 0.0
        screening, reasons = _screening_class(valid_share, settings, source)
        row: dict[str, object] = {
            "segment_id": segment.segment_id,
            "bathymetry_source_id": source.source_id,
            "bathymetry_release": source.source_release,
            "bathymetry_vertical_datum": source.vertical_datum,
            "bathymetry_native_resolution_m": source.native_resolution_m,
            "bathymetry_transect_count": count,
            "bathymetry_valid_transect_count": valid_count,
            "bathymetry_valid_transect_share": valid_share,
            "bathymetry_first_valid_distance_p50_m": _percentile(origins, 50),
            "bathymetry_first_valid_distance_p90_m": _percentile(origins, 90),
            "bathymetry_large_coastal_gap_share": (
                _share(segment_transects.bathymetry_origin_status == "large_coastal_gap")
                if count
                else 0.0
            ),
            "bathymetry_screening_class": screening,
            "bathymetry_screening_reasons": json.dumps(reasons, separators=(",", ":")),
            "bathymetry_quality_flag": (
                "insufficient"
                if screening == "insufficient"
                else "partial"
                if valid_share < 0.9
                else "usable_with_resolution_limits"
            ),
        }
        target_by_distance: dict[float, pd.DataFrame] = {}
        for distance in settings.target_distances_m:
            name = _name(distance)
            target = _target_rows(segment_samples, distance)
            target_by_distance[distance] = target
            values = target.loc[target.sample_valid, "depth_mean_m"]
            row[f"depth_{name}m_p10_m"] = _percentile(values, 10)
            row[f"depth_{name}m_p50_m"] = _percentile(values, 50)
            row[f"depth_{name}m_p90_m"] = _percentile(values, 90)
            row[f"depth_{name}m_valid_transect_share"] = len(values) / count if count else 0.0
            ratio = distance / source.native_resolution_m
            row[f"depth_{name}m_resolution_ratio"] = ratio
            row[f"depth_{name}m_resolution_class"] = resolution_class(
                ratio, settings.minimum_resolution_ratio
            )

        gradient_results: dict[str, list[float]] = {}
        for near, far in ((100.0, 500.0), (250.0, 1000.0)):
            key = f"gradient_{_name(near)}_{_name(far)}m"
            gradients: list[float] = []
            if near in target_by_distance and far in target_by_distance:
                near_rows = target_by_distance[near].set_index("bathymetry_transect_id")
                far_rows = target_by_distance[far].set_index("bathymetry_transect_id")
                for transect_id in near_rows.index.intersection(far_rows.index):
                    a = float(near_rows.loc[transect_id, "depth_mean_m"])
                    b = float(far_rows.loc[transect_id, "depth_mean_m"])
                    if (
                        np.isfinite(a)
                        and np.isfinite(b)
                        and near / source.native_resolution_m >= settings.minimum_resolution_ratio
                    ):
                        gradients.append((b - a) / (far - near))
            gradient_results[key] = gradients
            row[f"{key}_p50"] = _percentile(gradients, 50)
            row[f"{key}_p90"] = _percentile(gradients, 90)
        direction_gradients = gradient_results.get("gradient_100_500m", [])
        if direction_gradients:
            direction = np.asarray(direction_gradients)
            row["deepening_transect_share"] = float((direction > 0.0025).mean())
            row["shoaling_transect_share"] = float((direction < -0.0025).mean())
            row["flat_or_uncertain_transect_share"] = float((np.abs(direction) <= 0.0025).mean())
        else:
            row["deepening_transect_share"] = float("nan")
            row["shoaling_transect_share"] = float("nan")
            row["flat_or_uncertain_transect_share"] = float("nan")

        contour_hits: dict[float, list[float]] = {}
        for depth in settings.contour_depths_m:
            hits: list[float] = []
            for _, group in segment_samples[segment_samples.sample_valid].groupby(
                "bathymetry_transect_id"
            ):
                ordered = group.sort_values("distance_from_coast_m")
                reached = ordered[ordered.depth_mean_m >= depth]
                if len(reached):
                    hits.append(float(reached.iloc[0].distance_from_coast_m))
            contour_hits[depth] = hits
            name = _name(depth)
            row[f"distance_to_{name}m_depth_p50_m"] = _percentile(hits, 50)
            row[f"reaches_{name}m_depth_transect_share"] = len(hits) / count if count else 0.0
            row[f"distance_to_{name}m_depth_uncertainty_m"] = source.native_resolution_m

        for depth, distance in ((5.0, 250.0), (10.0, 500.0)):
            name = f"depth_below_{_name(depth)}m_at_{_name(distance)}m_share"
            if distance in target_by_distance:
                target = target_by_distance[distance]
                valid = target[target.sample_valid]
                row[name] = _share(valid.depth_mean_m < depth)
            else:
                row[name] = float("nan")
        contour_20 = contour_hits.get(20.0, [])
        row["rapid_deepening_to_20m_share"] = (
            float((np.asarray(contour_20) <= 250).sum() / count) if count else 0.0
        )
        platform_target = _target_rows(segment_samples, settings.shallow_platform_distance_m)
        platform_valid = platform_target[platform_target.sample_valid]
        platform_share = _share(platform_valid.depth_mean_m < settings.shallow_platform_depth_m)
        row["broad_shallow_platform_proxy"] = bool(
            np.isfinite(platform_share)
            and platform_share >= settings.shallow_platform_minimum_share
            and len(platform_valid) / count >= settings.minimum_valid_transect_share
            if count
            else False
        )

        valid_samples = segment_samples[segment_samples.sample_valid]
        flags = valid_samples.interpolation_flag.dropna()
        row["interpolated_cell_share"] = _share(flags == 1)
        row["extrapolated_cell_share"] = float("nan")
        row["measured_or_source_supported_cell_share"] = _share(flags == 0)
        references = valid_samples.source_reference.dropna()
        row["bathymetry_source_reference_count"] = int(references.nunique())
        row["bathymetry_dominant_source_reference"] = (
            float(references.mode().iloc[0]) if len(references) else float("nan")
        )
        row["bathymetry_dominant_source_type"] = "unknown"
        row["survey_source_share"] = 0.0
        row["composite_dtm_source_share"] = 0.0
        row["satellite_derived_source_share"] = 0.0
        row["global_fallback_source_share"] = float("nan")
        row["unknown_source_share"] = 1.0 if len(valid_samples) else float("nan")
        for field, output in (
            ("quality_index", "quality_index"),
            ("observation_count", "observation_count"),
            ("depth_std_m", "depth_std"),
        ):
            values = valid_samples[field].dropna()
            row[f"{output}_p10"] = _percentile(values, 10)
            row[f"{output}_p50"] = _percentile(values, 50)
            row[f"{output}_p90"] = _percentile(values, 90)
        rows.append(row)
    return pd.DataFrame(rows)
