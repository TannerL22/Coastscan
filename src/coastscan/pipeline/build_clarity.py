"""Additive Phase 3 optical build with explicit upstream regression guards."""

import json
import platform
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from coastscan import __version__
from coastscan.catalog.manifests import git_commit, sha256_file, sha256_text
from coastscan.config import PROJECT_ROOT, load_region_config
from coastscan.exceptions import MissingInputError, QualityThresholdError
from coastscan.io.outputs import write_geoparquet, write_parquet
from coastscan.models.manifests import OpticalRunManifest
from coastscan.optical.aggregation import aggregate_periods, best_month, headline_features
from coastscan.optical.cache import cached_outputs_are_valid, validate_acquisition_cache
from coastscan.optical.catalogue import discover_scene_catalogue
from coastscan.optical.observations import build_scene_features, extract_observations
from coastscan.optical.qa import (
    cross_layer_diagnostics,
    generate_optical_mask_qa,
    generate_optical_qa_figures,
    generate_optical_time_series_qa,
    optical_qa_summary,
)
from coastscan.optical.zones import zones_for_region

ALGORITHM_VERSION = "phase3-relative-clarity-2"
PROTECTED_FILES = (
    "coast_segments.parquet",
    "segment_features.parquet",
    "bathymetry_transects.parquet",
    "bathymetry_features.parquet",
    "segment_features_phase2.parquet",
)


def _checksums(processed: Path) -> dict[str, str]:
    missing = [name for name in PROTECTED_FILES if not (processed / name).is_file()]
    if missing:
        raise MissingInputError(
            "Phase 3 requires verified Phase 1/2 outputs: " + ", ".join(missing)
        )
    return {name: sha256_file(processed / name) for name in PROTECTED_FILES}


def _cache_key(
    config_path: Path,
    catalogue_path: Path,
    protected: dict[str, str],
    acquisition_manifest_checksum: str,
) -> str:
    payload = {
        "algorithm": ALGORITHM_VERSION,
        "configuration": sha256_file(config_path),
        "catalogue": sha256_file(catalogue_path),
        "protected": protected,
        "acquisition_manifest": acquisition_manifest_checksum,
    }
    return sha256_text(json.dumps(payload, sort_keys=True))


def _software_versions() -> dict[str, str]:
    result = {"python": platform.python_version(), "coastscan": __version__}
    for package in ("geopandas", "shapely", "rasterio", "numpy", "pandas", "pyarrow"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "unavailable"
    return result


def _latest_upstream_manifests(root: Path, region_id: str) -> tuple[str | None, str | None]:
    directory = root / "outputs" / "manifests" / region_id
    phase1 = sorted(
        path
        for path in directory.glob("*.json")
        if "bathymetry" not in path.name
        and "clarity" not in path.name
        and "optical" not in path.name
        and not path.name.startswith("latest_")
    )
    phase2 = sorted(directory.glob("*_bathymetry.json"))

    def relative(path: Path | None) -> str | None:
        return path.relative_to(root).as_posix() if path else None

    return relative(phase1[-1] if phase1 else None), relative(phase2[-1] if phase2 else None)


def build_clarity(
    region: str | Path,
    *,
    force: bool = False,
    write_observations: bool = False,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    timer = time.perf_counter()
    config, config_path = load_region_config(region, root)
    settings = config.optical
    if settings is None:
        raise MissingInputError(f"Optical analysis is not configured for {config.region_id}")
    processed = root / "data" / "processed" / config.region_id
    before = _checksums(processed)
    catalogue, catalogue_metadata = discover_scene_catalogue(region, root=root)
    catalogue_path = root / "data_catalog" / "optical" / f"{config.region_id}_scenes.parquet"
    cache_directory = root / "data" / "interim" / config.region_id / "optical" / "clips"
    selected = catalogue.loc[catalogue.selected]
    acquisition_cache = validate_acquisition_cache(
        root,
        config.region_id,
        selected,
        catalogue_checksum=sha256_file(catalogue_path),
    )
    cache_key = _cache_key(config_path, catalogue_path, before, acquisition_cache.manifest_checksum)
    manifest_path = root / "outputs" / "manifests" / config.region_id / "latest_clarity.json"
    phase3_path = processed / "segment_features_phase3.parquet"
    if not force and manifest_path.is_file() and phase3_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("cache_key") == cache_key and cached_outputs_are_valid(previous, root):
            return {**previous, "cache_used": True}
    segments = gpd.read_parquet(processed / "coast_segments.parquet")
    phase2 = gpd.read_parquet(processed / "segment_features_phase2.parquet")
    zone_started = time.perf_counter()
    zones = zones_for_region(config, segments, root)
    zone_seconds = time.perf_counter() - zone_started
    observation_started = time.perf_counter()
    observations = extract_observations(catalogue, zones, cache_directory, settings)
    scene_features = build_scene_features(catalogue, observations)
    observation_seconds = time.perf_counter() - observation_started
    if observations.empty:
        raise QualityThresholdError("No optical observations were extracted")
    aggregation_started = time.perf_counter()
    historical_observations = observations.loc[
        observations.analysis_period.astype(str) == "historical_baseline"
    ].copy()
    if historical_observations.empty:
        raise QualityThresholdError(
            "No valid historical-baseline optical observations were extracted"
        )
    seasonal = aggregate_periods(
        historical_observations,
        settings.periods,
        clear_threshold=settings.clarity.clear_percentile_threshold,
        turbid_threshold=settings.clarity.turbid_percentile_threshold,
        minimum_scenes=settings.clarity.minimum_valid_scenes,
        minimum_months=settings.clarity.minimum_valid_months,
        bottom_minimum_scenes=settings.bottom_texture.minimum_valid_scenes,
        bottom_minimum_persistence=settings.bottom_texture.minimum_cross_scene_persistence,
    )
    clarity = headline_features(seasonal).merge(best_month(seasonal), on="segment_id", how="left")
    all_ids = pd.DataFrame({"segment_id": segments.segment_id.astype(str)})
    clarity = all_ids.merge(clarity, on="segment_id", how="left", validate="one_to_one")
    clarity["clarity_data_confidence"] = clarity.clarity_data_confidence.fillna("insufficient")
    clarity["clarity_quality_flag"] = clarity.clarity_quality_flag.fillna("insufficient")
    phase3 = phase2.merge(clarity, on="segment_id", how="left", validate="one_to_one")
    aggregation_seconds = time.perf_counter() - aggregation_started
    outputs: list[Path] = [
        write_geoparquet(zones, processed / "clarity_zones.parquet"),
        write_parquet(scene_features, processed / "clarity_scenes.parquet"),
        write_parquet(seasonal, processed / "clarity_seasonal_features.parquet"),
        write_parquet(clarity, processed / "clarity_features.parquet"),
        write_geoparquet(phase3, phase3_path),
    ]
    partial_observations = observations.loc[
        observations.analysis_period.astype(str) == "partial_current_year"
    ].copy()
    partial_summary: dict[str, Any] | None = None
    if settings.include_partial_current_year and not partial_observations.empty:
        partial_seasonal = aggregate_periods(
            partial_observations,
            settings.periods,
            clear_threshold=settings.clarity.clear_percentile_threshold,
            turbid_threshold=settings.clarity.turbid_percentile_threshold,
            minimum_scenes=settings.clarity.minimum_valid_scenes,
            minimum_months=settings.clarity.minimum_valid_months,
            bottom_minimum_scenes=settings.bottom_texture.minimum_valid_scenes,
            bottom_minimum_persistence=settings.bottom_texture.minimum_cross_scene_persistence,
        )
        partial_seasonal["partial_period_label"] = settings.partial_year_label
        partial_path = processed / "clarity_current_period_features.parquet"
        outputs.append(write_parquet(partial_seasonal, partial_path))
        partial_summary = {
            "label": settings.partial_year_label,
            "scene_ids": sorted(partial_observations.scene_id.astype(str).unique()),
            "observation_count": len(partial_observations),
            "output_file": partial_path.relative_to(root).as_posix(),
        }
    if write_observations or settings.write_observations:
        outputs.append(write_parquet(observations, processed / "clarity_observations.parquet"))
    qa_directory = root / "outputs" / "qa" / config.region_id / "optical"
    qa_directory.mkdir(parents=True, exist_ok=True)
    qa_started = time.perf_counter()
    timings = {
        "zone_generation": zone_seconds,
        "observation_extraction": observation_seconds,
        "aggregation": aggregation_seconds,
    }
    qa = optical_qa_summary(catalogue, zones, observations, seasonal, clarity, timings=timings)
    qa["cross_layer_diagnostics"] = cross_layer_diagnostics(phase3)
    qa_path = qa_directory / "optical_qa_summary.json"
    outputs.extend(
        generate_optical_qa_figures(catalogue, segments, zones, seasonal, clarity, qa_directory)
    )
    outputs.extend(generate_optical_mask_qa(catalogue, cache_directory, qa_directory))
    time_series_path = generate_optical_time_series_qa(observations, clarity, qa_directory)
    if time_series_path is not None:
        outputs.append(time_series_path)
    timings["qa"] = time.perf_counter() - qa_started
    timings["total"] = time.perf_counter() - timer
    qa_path.write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    outputs.append(qa_path)
    after = _checksums(processed)
    if before != after:
        raise QualityThresholdError("A protected Phase 1/2 output changed during Phase 3")
    optical_source = config.inputs.optical
    assert optical_source is not None
    run_id = f"{started.strftime('%Y%m%dT%H%M%S%f')}Z_{config.region_id}_clarity"
    upstream_phase1_manifest, upstream_phase2_manifest = _latest_upstream_manifests(
        root, config.region_id
    )
    warning_counts = {
        "invalid_observations": int((~observations.valid.astype(bool)).sum()),
        "insufficient_segments": int(
            clarity.clarity_data_confidence.astype(str).eq("insufficient").sum()
        ),
    }
    manifest_model = OpticalRunManifest(
        run_id=run_id,
        region_id=config.region_id,
        pipeline_version=__version__,
        algorithm_version=ALGORITHM_VERSION,
        git_commit=git_commit(root),
        started_at_utc=started.isoformat(),
        completed_at_utc=datetime.now(UTC).isoformat(),
        status="success" if qa["passed"] else "quality_failed",
        configuration_path=config_path.relative_to(root).as_posix(),
        configuration_checksum=sha256_file(config_path),
        upstream_phase1_manifest=upstream_phase1_manifest,
        upstream_phase2_manifest=upstream_phase2_manifest,
        upstream_segment_file=(processed / "coast_segments.parquet").relative_to(root).as_posix(),
        upstream_segment_checksum=before["coast_segments.parquet"],
        upstream_segment_id_set_checksum=sha256_text(
            "\n".join(sorted(segments.segment_id.astype(str)))
        ),
        upstream_phase2_file=(processed / "segment_features_phase2.parquet")
        .relative_to(root)
        .as_posix(),
        upstream_phase2_checksum=before["segment_features_phase2.parquet"],
        upstream_phase2_feature_checksum=before["segment_features_phase2.parquet"],
        protected_upstream_checksums=before,
        provider="Copernicus Data Space Ecosystem",
        optical_provider="Copernicus Data Space Ecosystem",
        catalogue_endpoint=optical_source.catalogue_endpoint,
        catalogue_endpoint_reference=optical_source.catalogue_endpoint,
        collection=optical_source.collection,
        licence="Copernicus Sentinel Data Legal Notice under CDSE terms",
        required_attribution=(
            "Contains modified Copernicus Sentinel-2 data "
            f"({settings.historical_start.year}-{settings.historical_end.year}), accessed "
            "through the Copernicus Data Space Ecosystem."
        ),
        historical_period=[
            settings.historical_start.isoformat(),
            settings.historical_end.isoformat(),
        ],
        partial_current_period=partial_summary,
        selected_scene_ids=selected.scene_id.astype(str).tolist(),
        scene_catalogue_file=catalogue_path.relative_to(root).as_posix(),
        scene_catalogue_checksum=sha256_file(catalogue_path),
        processing_baselines={
            str(key): int(value)
            for key, value in selected.processing_baseline.value_counts().items()
        },
        asset_mapping=optical_source.required_assets.model_dump(),
        radiometric_method="per-asset STAC scale and offset; nodata preserved",
        mask_method_versions={
            "scl": "scl-classes-v1",
            "spectral_water": "green-nir-swir-v1",
            "dark_shadow": "dark-water-v1",
            "whitewater": "visible-whiteness-v1",
            "glint_risk": "nir-swir-v1",
            "vector_land": "authoritative-land-buffer-v1",
        },
        clarity_formula=(
            "mean of direction-aware within-scene/zone percentiles for blue-green ratio, "
            "negative NDTI and negative NIR"
        ),
        bottom_texture_method=(
            "gradient-strength candidates with valid-clear-scene and foam/glint gating; "
            "published as insufficient unless spatial cross-scene repeatability is verified"
        ),
        zone_configuration=settings.zones.model_dump(),
        acquisition_manifest=acquisition_cache.manifest_path.relative_to(root).as_posix(),
        acquisition_manifest_checksum=acquisition_cache.manifest_checksum,
        acquired_clip_count=acquisition_cache.file_count,
        acquired_clip_bytes=acquisition_cache.total_bytes,
        output_files=[path.relative_to(root).as_posix() for path in outputs],
        output_checksums={path.relative_to(root).as_posix(): sha256_file(path) for path in outputs},
        feature_counts={
            "segments": len(segments),
            "segment_zones": len(zones),
            "scenes": len(scene_features),
            "observations": len(observations),
            "valid_observations": int(observations.valid.astype(bool).sum()),
            "seasonal_rows": len(seasonal),
            "clarity_feature_rows": len(clarity),
        },
        warning_counts=warning_counts,
        quality_results=qa,
        software_versions=_software_versions(),
        cache_key=cache_key,
        cache_used=False,
        catalogue_metadata=catalogue_metadata,
    )
    manifest: dict[str, Any] = manifest_model.model_dump(mode="json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, indent=2, sort_keys=True)
    timestamped_manifest = manifest_path.parent / f"{run_id}.json"
    timestamped_manifest.write_text(payload, encoding="utf-8")
    manifest_path.write_text(payload, encoding="utf-8")
    if not qa["passed"]:
        raise QualityThresholdError("Phase 3 outputs were generated but optical QA failed")
    return manifest
