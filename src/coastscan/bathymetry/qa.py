"""Automated and visual QA for Phase 2 bathymetry proxies."""

from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

from coastscan.bathymetry.prepare import PreparedBathymetry
from coastscan.models.region import BathymetryConfig, BathymetryInput

SCREENING_CLASSES = {
    "local_morphology_candidate",
    "coastal_context",
    "regional_screening",
    "background_only",
    "insufficient",
}


def run_bathymetry_qa(
    segments: gpd.GeoDataFrame,
    transects: gpd.GeoDataFrame,
    samples: pd.DataFrame,
    features: pd.DataFrame,
    settings: BathymetryConfig,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    checks["transect_ids_unique"] = bool(transects.bathymetry_transect_id.is_unique)
    checks["transect_lengths_match"] = bool(
        np.allclose(transects.length, settings.maximum_offshore_distance_m, atol=0.01)
    )
    ambiguous_ids = set(segments.loc[segments.orientation_status != "resolved", "segment_id"])
    checks["no_transects_for_ambiguous_orientation"] = not bool(
        set(transects.segment_id) & ambiguous_ids
    )
    depths = samples.loc[samples.sample_valid, "depth_mean_m"]
    checks["canonical_depth_nonnegative"] = bool((depths >= 0).all())
    invalid_samples = samples.loc[~samples.sample_valid, "depth_mean_m"]
    checks["nodata_not_converted_to_zero"] = bool(invalid_samples.isna().all())
    share_columns = [str(column) for column in features.columns if str(column).endswith("_share")]
    checks["shares_in_unit_interval"] = all(
        bool(features[column].dropna().between(0, 1).all()) for column in share_columns
    )
    percentile_ok = True
    for distance in settings.target_distances_m:
        name = str(int(distance)) if float(distance).is_integer() else str(distance)
        columns = [f"depth_{name}m_p10_m", f"depth_{name}m_p50_m", f"depth_{name}m_p90_m"]
        valid = features[columns].dropna()
        percentile_ok &= bool(
            (
                (valid[columns[0]] <= valid[columns[1]]) & (valid[columns[1]] <= valid[columns[2]])
            ).all()
        )
    checks["depth_percentiles_ordered"] = percentile_ok
    checks["screening_classes_valid"] = bool(
        set(features.bathymetry_screening_class.unique()) <= SCREENING_CLASSES
    )
    ambiguous_features = features[features.segment_id.isin(ambiguous_ids)]
    checks["ambiguous_segments_have_no_morphology"] = bool(
        (ambiguous_features.bathymetry_screening_class == "insufficient").all()
        and (ambiguous_features.bathymetry_transect_count == 0).all()
    )
    contour_columns = [
        str(column)
        for column in features.columns
        if str(column).startswith("distance_to_") and str(column).endswith("_p50_m")
    ]
    checks["contour_distances_within_transects"] = all(
        bool(features[column].dropna().between(0, settings.maximum_offshore_distance_m).all())
        for column in contour_columns
    )
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not failed,
        "failed_checks": failed,
        "checks": checks,
        "counts": {
            "segments": len(segments),
            "bathymetry_transects": len(transects),
            "valid_bathymetry_transects": int(
                (transects.bathymetry_origin_status != "no_valid_bathymetry").sum()
            ),
            "segments_with_valid_features": int(
                (features.bathymetry_valid_transect_count > 0).sum()
            ),
            "orientation_excluded_segments": len(ambiguous_ids),
            "large_coastal_gap_transects": int(
                (transects.bathymetry_origin_status == "large_coastal_gap").sum()
            ),
        },
        "screening_class_counts": {
            str(key): int(value)
            for key, value in features.bathymetry_screening_class.value_counts().items()
        },
    }


def _title(source: BathymetryInput, label: str) -> str:
    return (
        f"{label}\n{source.source_id} release {source.source_release}; "
        f"native {source.native_resolution_m:g} m; {source.vertical_datum}\n"
        "Regional bathymetry proxies — not site-safety evidence"
    )


def generate_bathymetry_maps(
    segments: gpd.GeoDataFrame,
    transects: gpd.GeoDataFrame,
    samples: pd.DataFrame,
    features: pd.DataFrame,
    prepared: PreparedBathymetry,
    source: BathymetryInput,
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    with rasterio.open(prepared.paths["depth_mean_m"]) as dataset:
        array = dataset.read(1)
        extent = (
            float(dataset.bounds.left),
            float(dataset.bounds.right),
            float(dataset.bounds.bottom),
            float(dataset.bounds.top),
        )
    fig, axis = plt.subplots(figsize=(10, 8))
    image = axis.imshow(array, extent=extent, origin="upper", cmap="Blues", alpha=0.8)
    transects.plot(ax=axis, linewidth=0.15, color="black", alpha=0.4)
    segments.plot(ax=axis, linewidth=0.7, color="orange")
    fig.colorbar(image, ax=axis, label="Depth (m positive down)")
    axis.set_title(_title(source, "Bathymetry coverage and transects"))
    path = output_dir / "bathymetry_coverage.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    outputs.append(path)

    feature_map = segments[["segment_id", "geometry"]].merge(features, on="segment_id")
    map_fields = [
        "bathymetry_first_valid_distance_p50_m",
        "depth_250m_p50_m",
        "depth_500m_p50_m",
        "depth_1000m_p50_m",
        "gradient_100_500m_p50",
        "interpolated_cell_share",
    ]
    for field in map_fields:
        if field not in feature_map:
            continue
        fig, axis = plt.subplots(figsize=(10, 8))
        feature_map.plot(
            column=field, ax=axis, legend=True, linewidth=2, missing_kwds={"color": "lightgrey"}
        )
        axis.set_title(_title(source, field))
        axis.set_axis_off()
        path = output_dir / f"{field}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        outputs.append(path)

    valid = samples[samples.sample_valid]
    fig, axis = plt.subplots(figsize=(10, 6))
    representatives = sorted(valid.bathymetry_transect_id.unique())[:10]
    for transect_id in representatives:
        group = valid[valid.bathymetry_transect_id == transect_id].sort_values(
            "distance_from_coast_m"
        )
        axis.plot(group.distance_from_coast_m, group.depth_mean_m, alpha=0.7)
    axis.invert_yaxis()
    axis.set_xlabel("Distance offshore (m)")
    axis.set_ylabel("Depth (m positive down)")
    axis.set_title(_title(source, "Representative deterministic cross-sections"))
    path = output_dir / "bathymetry_cross_sections.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    outputs.append(path)
    return outputs
