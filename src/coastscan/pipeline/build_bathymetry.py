"""Independent end-to-end Phase 2 regional bathymetry build."""

import json
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np

from coastscan import __version__
from coastscan.bathymetry.features import calculate_bathymetry_features
from coastscan.bathymetry.prepare import (
    bathymetry_cache_key,
    inspect_bathymetry_source,
    prepare_bathymetry,
    valid_bathymetry_cache_exists,
)
from coastscan.bathymetry.qa import generate_bathymetry_maps, run_bathymetry_qa
from coastscan.bathymetry.sampling import sample_bathymetry
from coastscan.bathymetry.transects import generate_bathymetry_transects
from coastscan.catalog.manifests import git_commit, sha256_file, sha256_text
from coastscan.config import PROJECT_ROOT, load_region_config
from coastscan.exceptions import MissingInputError, QualityThresholdError, RasterValidationError
from coastscan.io.outputs import write_geoparquet, write_parquet
from coastscan.models.manifests import BathymetryRunManifest


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _software_versions() -> dict[str, str]:
    result = {"python": platform.python_version(), "coastscan": __version__}
    for package in ("geopandas", "shapely", "rasterio", "numpy", "pandas", "pyarrow"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "unavailable"
    return result


def _phase1_paths(region_id: str, root: Path) -> tuple[Path, Path, Path]:
    processed = root / "data" / "processed" / region_id
    segments = processed / "coast_segments.parquet"
    features = processed / "segment_features.parquet"
    manifests = root / "outputs" / "manifests" / region_id
    candidates = sorted(
        path for path in manifests.glob("*.json") if not path.name.endswith("_bathymetry.json")
    )
    if not segments.is_file() or not features.is_file() or not candidates:
        raise MissingInputError(
            f"Verified Phase 1 outputs are required before Phase 2 for {region_id}"
        )
    return segments, features, candidates[-1]


def _verify_phase1_segment_contract(manifest_path: Path, segment_path: Path, root: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = _relative(segment_path, root)
    expected = manifest.get("output_checksums", {}).get(relative)
    actual = sha256_file(segment_path)
    if expected != actual:
        raise RasterValidationError(
            "Phase 1 segment output is stale or changed relative to its latest manifest: "
            f"expected {expected}, got {actual}"
        )


def inspect_bathymetry(region: str | Path, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    config, config_path = load_region_config(region, root)
    if config.inputs.bathymetry is None or config.bathymetry is None:
        raise RasterValidationError(f"Bathymetry is not configured for {config.region_id}")
    segment_path, _, phase1_manifest = _phase1_paths(config.region_id, root)
    _verify_phase1_segment_contract(phase1_manifest, segment_path, root)
    segments = gpd.read_parquet(segment_path)
    details = inspect_bathymetry_source(config, segments, root)
    segment_checksum = sha256_file(segment_path)
    details.update(
        {
            "configuration_path": _relative(config_path, root),
            "upstream_phase1_manifest": _relative(phase1_manifest, root),
            "upstream_segment_checksum": segment_checksum,
            "upstream_segment_id_set_checksum": sha256_text(
                "\n".join(sorted(segments.segment_id.astype(str)))
            ),
            "bathymetry_cache_key": bathymetry_cache_key(config, segments, root),
            "valid_bathymetry_cache_exists": valid_bathymetry_cache_exists(config, segments, root),
        }
    )
    return details


def _cross_layer_diagnostics(phase2: gpd.GeoDataFrame) -> dict[str, Any]:
    relief = phase2.get("land_relief_100m_p90_m")
    gradient = phase2.get("gradient_100_500m_p50")
    if relief is None or gradient is None:
        return {"available": False}
    valid = phase2[np.isfinite(relief) & np.isfinite(gradient)]
    if not len(valid):
        return {"available": False}
    relief_cut = float(valid.land_relief_100m_p90_m.median())
    gradient_cut = float(valid.gradient_100_500m_p50.median())
    return {
        "available": True,
        "descriptive_only": True,
        "safety_boundary": (
            "These combinations are plausibility diagnostics, not an opportunity or safety score."
        ),
        "relief_median_m": relief_cut,
        "gradient_median": gradient_cut,
        "high_relief_rapid_deepening_count": int(
            (
                (valid.land_relief_100m_p90_m >= relief_cut)
                & (valid.gradient_100_500m_p50 >= gradient_cut)
            ).sum()
        ),
        "high_relief_slow_deepening_count": int(
            (
                (valid.land_relief_100m_p90_m >= relief_cut)
                & (valid.gradient_100_500m_p50 < gradient_cut)
            ).sum()
        ),
        "low_relief_rapid_deepening_count": int(
            (
                (valid.land_relief_100m_p90_m < relief_cut)
                & (valid.gradient_100_500m_p50 >= gradient_cut)
            ).sum()
        ),
    }


def build_bathymetry(
    region: str | Path,
    *,
    force: bool = False,
    write_samples: bool = False,
    skip_qa_map: bool = False,
    verbose: bool = False,
    root: Path = PROJECT_ROOT,
) -> BathymetryRunManifest:
    del verbose
    started = datetime.now(UTC)
    started_perf = time.perf_counter()
    run_id = f"{started.strftime('%Y%m%dT%H%M%S%f')}Z_{Path(str(region)).stem}"
    config, config_path = load_region_config(region, root)
    source = config.inputs.bathymetry
    settings = config.bathymetry
    if source is None or settings is None:
        raise RasterValidationError(f"Bathymetry is not configured for {config.region_id}")
    segment_path, phase1_feature_path, phase1_manifest = _phase1_paths(config.region_id, root)
    _verify_phase1_segment_contract(phase1_manifest, segment_path, root)
    segments = gpd.read_parquet(segment_path)
    phase1_features = gpd.read_parquet(phase1_feature_path)
    if str(segments.crs) != str(phase1_features.crs):
        raise RasterValidationError("Phase 1 segment and feature CRS do not match")
    segment_checksum = sha256_file(segment_path)
    segment_id_checksum = sha256_text("\n".join(sorted(segments.segment_id.astype(str))))
    prepared = prepare_bathymetry(config, segments, root, force=force)
    transects = generate_bathymetry_transects(
        segments,
        spacing_m=settings.transect_spacing_m,
        maximum_distance_m=settings.maximum_offshore_distance_m,
    )
    transects, samples = sample_bathymetry(transects, prepared, settings)
    features = calculate_bathymetry_features(segments, transects, samples, settings, source)
    phase2 = phase1_features.merge(features, on="segment_id", how="left", validate="one_to_one")

    processed = root / "data" / "processed" / config.region_id
    interim = root / "data" / "interim" / config.region_id
    qa_dir = root / "outputs" / "qa" / config.region_id / "bathymetry"
    manifest_dir = root / "outputs" / "manifests" / config.region_id
    output_paths: list[Path] = []
    output_paths.append(write_geoparquet(transects, processed / "bathymetry_transects.parquet"))
    output_paths.append(write_parquet(features, processed / "bathymetry_features.parquet"))
    output_paths.append(write_geoparquet(phase2, processed / "segment_features_phase2.parquet"))
    if write_samples or settings.write_samples:
        output_paths.append(write_parquet(samples, interim / "bathymetry_samples.parquet"))
    audit = (
        samples.dropna(subset=["source_reference"])
        .groupby(["source_reference", "source_type"], as_index=False)
        .agg(sample_count=("sample_valid", "size"), valid_sample_count=("sample_valid", "sum"))
        .sort_values(["source_reference", "source_type"])
    )
    output_paths.append(
        write_parquet(audit, processed / "bathymetry_source_reference_audit.parquet")
    )
    qa_summary = run_bathymetry_qa(segments, transects, samples, features, settings)
    qa_summary.update(
        {
            "source": inspect_bathymetry_source(config, segments, root),
            "cache_used": prepared.cache_used,
            "cache_key": prepared.cache_key,
            "cross_layer_diagnostics": _cross_layer_diagnostics(phase2),
            "first_valid_distance_m": {
                "p50": float(transects.first_valid_depth_distance_m.quantile(0.5)),
                "p90": float(transects.first_valid_depth_distance_m.quantile(0.9)),
                "maximum": float(transects.first_valid_depth_distance_m.max()),
            },
            "runtime_seconds": time.perf_counter() - started_perf,
        }
    )
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_summary_path = qa_dir / "bathymetry_qa_summary.json"
    qa_summary_path.write_text(json.dumps(qa_summary, indent=2, sort_keys=True), encoding="utf-8")
    output_paths.append(qa_summary_path)
    if not skip_qa_map:
        output_paths.extend(
            generate_bathymetry_maps(
                segments, transects, samples, features, prepared, source, qa_dir
            )
        )
    completed = datetime.now(UTC)
    counts = {
        "segments": len(segments),
        "bathymetry_transects": len(transects),
        "bathymetry_samples": len(samples),
        "bathymetry_feature_rows": len(features),
        "segments_with_valid_bathymetry": int((features.bathymetry_valid_transect_count > 0).sum()),
    }
    warnings: list[str] = []
    if source.screening_class_ceiling == "background_only":
        warnings.append("source_assessment: background-only ceiling applies")
    if features.global_fallback_source_share.isna().all():
        warnings.append("source_reference: official CDI index lookup unavailable in subset")
    manifest = BathymetryRunManifest(
        run_id=run_id,
        region_id=config.region_id,
        pipeline_version=__version__,
        git_commit=git_commit(root),
        started_at_utc=started.isoformat(),
        completed_at_utc=completed.isoformat(),
        status="success" if qa_summary["passed"] else "quality_failed",
        configuration_path=_relative(config_path, root),
        configuration_checksum=sha256_file(config_path),
        upstream_phase1_manifest=_relative(phase1_manifest, root),
        upstream_segment_file=_relative(segment_path, root),
        upstream_segment_checksum=segment_checksum,
        upstream_segment_id_set_checksum=segment_id_checksum,
        bathymetry_sources=[_relative(prepared.source_path, root)],
        bathymetry_source_checksums={
            _relative(prepared.source_path, root): prepared.source_checksum
        },
        source_release=source.source_release,
        vertical_datum=source.vertical_datum,
        native_resolution=[prepared.native_effective_resolution_m] * 2,
        output_resolution=list(prepared.output_resolution),
        variable_mapping=prepared.variables,
        output_files=[_relative(path, root) for path in output_paths],
        output_checksums={_relative(path, root): sha256_file(path) for path in output_paths},
        feature_counts=counts,
        warning_counts=dict(Counter(item.split(":", 1)[0] for item in warnings)),
        quality_results=qa_summary,
        software_versions=_software_versions(),
        cache_key=prepared.cache_key,
        cache_used=prepared.cache_used,
    )
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{run_id}_bathymetry.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8"
    )
    if not qa_summary["passed"]:
        raise QualityThresholdError(
            "Phase 2 outputs were generated but bathymetry QA failed: "
            + ", ".join(qa_summary["failed_checks"])
        )
    return manifest
