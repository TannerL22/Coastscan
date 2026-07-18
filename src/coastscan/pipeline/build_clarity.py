"""Additive Phase 3 optical build with explicit upstream regression guards."""

import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from coastscan import __version__
from coastscan.catalog.manifests import git_commit, sha256_file, sha256_text
from coastscan.config import PROJECT_ROOT, load_region_config
from coastscan.exceptions import MissingInputError, QualityThresholdError
from coastscan.io.outputs import write_geoparquet, write_parquet
from coastscan.optical.aggregation import aggregate_periods, best_month, headline_features
from coastscan.optical.catalogue import discover_scene_catalogue
from coastscan.optical.observations import extract_observations
from coastscan.optical.zones import zones_for_region

ALGORITHM_VERSION = "phase3-relative-clarity-1"
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


def _cache_key(config_path: Path, catalogue_path: Path, protected: dict[str, str]) -> str:
    payload = {
        "algorithm": ALGORITHM_VERSION,
        "configuration": sha256_file(config_path),
        "catalogue": sha256_file(catalogue_path),
        "protected": protected,
    }
    return sha256_text(json.dumps(payload, sort_keys=True))


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
    cache_key = _cache_key(config_path, catalogue_path, before)
    cache_directory = root / "data" / "interim" / config.region_id / "optical" / "clips"
    selected = catalogue.loc[catalogue.selected]
    required = [cache_directory / str(scene) / "blue.tif" for scene in selected.scene_id]
    absent = [path for path in required if not path.is_file()]
    if absent:
        raise MissingInputError(
            f"Official optical clips are absent for {len(absent)} selected scenes. Run "
            f"'uv run coastscan acquire-optical --region {config.region_id}' after configuring "
            "CDSE S3 credentials. No unofficial imagery substitute was used."
        )
    manifest_path = root / "outputs" / "manifests" / config.region_id / "latest_optical.json"
    phase3_path = processed / "segment_features_phase3.parquet"
    if not force and manifest_path.is_file() and phase3_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("cache_key") == cache_key:
            return {**previous, "cache_used": True}
    segments = gpd.read_parquet(processed / "coast_segments.parquet")
    phase2 = gpd.read_parquet(processed / "segment_features_phase2.parquet")
    zones = zones_for_region(config, segments, root)
    observations = extract_observations(catalogue, zones, cache_directory, settings)
    if observations.empty:
        raise QualityThresholdError("No optical observations were extracted")
    seasonal = aggregate_periods(
        observations,
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
    outputs: list[Path] = [
        write_geoparquet(zones, processed / "optical_zones.parquet"),
        write_parquet(seasonal, processed / "clarity_seasonal_features.parquet"),
        write_parquet(clarity, processed / "clarity_features.parquet"),
        write_geoparquet(phase3, phase3_path),
    ]
    if write_observations or settings.write_observations:
        outputs.append(write_parquet(observations, processed / "clarity_observations.parquet"))
    qa_directory = root / "outputs" / "qa" / config.region_id / "optical"
    qa_directory.mkdir(parents=True, exist_ok=True)
    qa = {
        "candidate_scenes": len(catalogue),
        "selected_scenes": len(selected),
        "segment_zone_count": len(zones),
        "valid_observations": int(observations.valid.sum()),
        "invalid_reasons": dict(Counter(observations.loc[~observations.valid, "invalid_reason"])),
        "confidence_classes": clarity.clarity_data_confidence.value_counts().to_dict(),
        "runtime_seconds": time.perf_counter() - timer,
        "interpretation_boundary": (
            "Historical region-relative coastal-water screening only; no current condition, "
            "physical visibility depth, underwater-clearance, suitability, or safety claim."
        ),
    }
    qa_path = qa_directory / "optical_qa_summary.json"
    qa_path.write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    outputs.append(qa_path)
    after = _checksums(processed)
    if before != after:
        raise QualityThresholdError("A protected Phase 1/2 output changed during Phase 3")
    manifest: dict[str, Any] = {
        "run_id": f"{started.strftime('%Y%m%dT%H%M%S%f')}Z_{config.region_id}_optical",
        "region_id": config.region_id,
        "pipeline_version": __version__,
        "algorithm_version": ALGORITHM_VERSION,
        "git_commit": git_commit(root),
        "started_at_utc": started.isoformat(),
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "status": "success",
        "provider": "Copernicus Data Space Ecosystem",
        "collection": config.inputs.optical.collection if config.inputs.optical else None,
        "historical_period": [
            settings.historical_start.isoformat(),
            settings.historical_end.isoformat(),
        ],
        "scene_catalogue_checksum": sha256_file(catalogue_path),
        "selected_scene_ids": selected.scene_id.astype(str).tolist(),
        "processing_baselines": selected.processing_baseline.value_counts().to_dict(),
        "cache_key": cache_key,
        "cache_used": False,
        "protected_upstream_checksums": before,
        "output_files": [path.relative_to(root).as_posix() for path in outputs],
        "output_checksums": {
            path.relative_to(root).as_posix(): sha256_file(path) for path in outputs
        },
        "quality_results": qa,
        "secrets_recorded": False,
        "catalogue_metadata": catalogue_metadata,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest
