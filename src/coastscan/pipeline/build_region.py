"""End-to-end deterministic Phase 1 regional build."""

import json
import logging
import platform
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import geopandas as gpd
import rasterio
from shapely.geometry import box

from coastscan import __version__
from coastscan.catalog.manifests import git_commit, sha256_file, sha256_text, write_manifest
from coastscan.catalog.sources import load_source_catalog, source_metadata_warnings
from coastscan.coastline.clean import clean_coastline
from coastscan.coastline.extract import extract_coastline
from coastscan.coastline.orientation import orient_segments
from coastscan.coastline.segment import segment_coastline
from coastscan.coastline.transects import generate_transects
from coastscan.config import PROJECT_ROOT, data_path, load_region_config
from coastscan.exceptions import QualityThresholdError, RasterValidationError
from coastscan.io.outputs import write_geoparquet, write_parquet
from coastscan.io.rasters import prepare_terrain
from coastscan.io.vectors import load_land
from coastscan.logging_config import configure_logging
from coastscan.models.manifests import RunManifest
from coastscan.qa.checks import run_qa_checks
from coastscan.qa.maps import generate_qa_artifacts
from coastscan.qa.reports import write_qa_report, write_qa_summary
from coastscan.terrain.features import calculate_terrain_features
from coastscan.terrain.sampling import sample_terrain


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


def inspect_region_inputs(region: str | Path, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Validate mandatory local inputs and return useful source metadata."""
    config, config_path = load_region_config(region, root)
    land_path = data_path(config.inputs.land_polygon.path, root)
    dem_path = data_path(config.inputs.elevation.path, root)
    land = load_land(land_path, config.inputs.land_polygon.layer, config.analysis_crs)
    if not dem_path.is_file():
        from coastscan.exceptions import MissingInputError

        raise MissingInputError(
            f"Missing required elevation raster: {dem_path}\n"
            f"Add the configured raster or update: {config_path}"
        )
    with rasterio.open(dem_path) as dem:
        if dem.crs is None:
            raise RasterValidationError(f"Elevation raster has no CRS: {dem_path}")
        if dem.transform.is_identity or dem.transform.a == 0 or dem.transform.e == 0:
            raise RasterValidationError(
                f"Elevation raster has invalid affine transform: {dem_path}"
            )
        source_land = (
            gpd.GeoSeries([land.geometry], crs=config.analysis_crs).to_crs(dem.crs).iloc[0]
        )
        if not source_land.intersects(box(*dem.bounds)):
            raise RasterValidationError(
                "Elevation raster does not overlap the configured land region"
            )
        raster_info = {
            "crs": str(dem.crs),
            "resolution": [abs(dem.res[0]), abs(dem.res[1])],
            "nodata": dem.nodata,
            "size": [dem.width, dem.height],
        }
    catalog_path = root / "data_catalog" / "sources.csv"
    catalog = load_source_catalog(catalog_path)
    warnings: list[str] = []
    for source_id in (config.inputs.land_polygon.source_id, config.inputs.elevation.source_id):
        warnings.extend(
            f"{source_id}: {warning}"
            for warning in source_metadata_warnings(catalog.get(source_id))
        )
    return {
        "region_id": config.region_id,
        "configuration": _relative(config_path, root),
        "land": {
            "path": _relative(land_path, root),
            "feature_count": land.feature_count,
            "original_crs": land.original_crs,
            "analysis_crs": config.analysis_crs,
            "originally_valid": land.originally_valid,
            "repair_applied": land.repair_applied,
            "area_m2": land.final_area_m2,
            "geometry_type": land.geometry.geom_type,
        },
        "elevation": {"path": _relative(dem_path, root), **raster_info},
        "source_warnings": warnings,
    }


def build_region(
    region: str | Path,
    *,
    root: Path = PROJECT_ROOT,
    force: bool = False,
    write_samples: bool = False,
    skip_qa_map: bool = False,
    verbose: bool = False,
) -> RunManifest:
    """Execute all Phase 1 stages and return the persisted run manifest."""
    config, config_path = load_region_config(region, root)
    started = datetime.now(UTC)
    run_id = f"{started.strftime('%Y%m%dT%H%M%S%fZ')}_{config.region_id}"
    manifest_dir = root / "outputs" / "manifests" / config.region_id
    manifest_path = manifest_dir / f"{run_id}.json"
    log_path = manifest_dir / f"{run_id}.log"
    logger = configure_logging(log_path, verbose)
    config_checksum = sha256_file(config_path)
    land_path = data_path(config.inputs.land_polygon.path, root)
    dem_source_path = data_path(config.inputs.elevation.path, root)
    logger.info("stage=input loading region=%s", config.region_id)
    land = load_land(land_path, config.inputs.land_polygon.layer, config.analysis_crs)
    source_checksum = sha256_file(land_path)
    if not dem_source_path.is_file():
        from coastscan.exceptions import MissingInputError

        raise MissingInputError(
            f"Missing required elevation raster: {dem_source_path}\n"
            f"Add the configured raster or update: {config_path}"
        )
    dem_checksum = sha256_file(dem_source_path)
    relevant_config = json.dumps(config.coastline.model_dump(), sort_keys=True)
    coastline_version = sha256_text(f"{config.region_id}:{source_checksum}:{relevant_config}")[:12]
    warnings: list[str] = []
    catalog = load_source_catalog(root / "data_catalog" / "sources.csv")
    for source_id in (config.inputs.land_polygon.source_id, config.inputs.elevation.source_id):
        warnings.extend(
            f"{source_id}: {warning}"
            for warning in source_metadata_warnings(catalog.get(source_id))
        )
    if land.repair_applied:
        warnings.append("Land geometry was repaired with shapely.make_valid")

    logger.info("stage=coastline extracting boundary")
    coastline = extract_coastline(
        land.geometry,
        region_id=config.region_id,
        source_id=config.inputs.land_polygon.source_id,
        source_checksum=source_checksum,
        processing_version=coastline_version,
        crs=config.analysis_crs,
        include_interior=config.coastline.include_interior_shorelines,
    )
    coastline, cleaning = clean_coastline(coastline, config.coastline.simplification_tolerance_m)
    if cleaning.warning:
        warnings.append(cleaning.warning)
    interim = root / "data" / "interim" / config.region_id
    processed = root / "data" / "processed" / config.region_id
    qa_dir = root / "outputs" / "qa" / config.region_id
    report_dir = root / "outputs" / "reports" / config.region_id
    output_paths: list[Path] = []
    output_paths.append(write_geoparquet(coastline, interim / "coastline_clean.parquet"))

    logger.info("stage=segmentation coastline_parts=%d", len(coastline))
    segments = segment_coastline(
        coastline,
        region_id=config.region_id,
        coastline_version=coastline_version,
        target_length_m=config.coastline.target_segment_length_m,
        minimum_length_m=config.coastline.minimum_segment_length_m,
    )
    segments = orient_segments(
        segments,
        land.geometry,
        config.coastline.orientation_test_distance_m,
        config.coastline.orientation_fallback_distances_m,
    )
    transects = generate_transects(
        segments,
        config.transects.spacing_m,
        config.transects.inland_length_m,
        config.transects.offshore_length_m,
    )
    output_paths.append(write_geoparquet(segments, processed / "coast_segments.parquet"))
    output_paths.append(write_geoparquet(transects, processed / "transects.parquet"))

    logger.info("stage=terrain preparing raster")
    terrain = prepare_terrain(
        dem_source_path,
        land.geometry,
        config.analysis_crs,
        interim,
        config.terrain.roughness_window_m,
        force=force,
    )
    logger.info("stage=terrain cache_used=%s cache_key=%s", terrain.cache_used, terrain.cache_key)
    output_paths.extend([terrain.dem_path, terrain.slope_path, terrain.roughness_path])
    logger.info(
        "stage=terrain_sampling inland_transects=%d", int((transects.direction == "inland").sum())
    )
    samples = sample_terrain(
        transects,
        terrain.dem_path,
        terrain.slope_path,
        terrain.roughness_path,
        config.terrain.sample_spacing_m,
    )
    if write_samples or config.terrain.write_samples:
        output_paths.append(write_parquet(samples, interim / "terrain_samples.parquet"))
    features = calculate_terrain_features(
        segments,
        transects,
        samples,
        config.terrain.relief_distances_m,
        config.terrain.sample_spacing_m,
        config.terrain.steep_slope_threshold_degrees,
        config.terrain.minimum_valid_sample_share,
    )
    terrain_features_path = write_parquet(features, processed / "terrain_features.parquet")
    output_paths.append(terrain_features_path)
    segment_features = gpd.GeoDataFrame(
        segments.merge(features, on="segment_id", how="left"), geometry="geometry", crs=segments.crs
    )
    output_paths.append(write_geoparquet(segment_features, processed / "segment_features.parquet"))

    logger.info("stage=qa checks")
    qa_summary = run_qa_checks(
        land.geometry,
        coastline,
        segments,
        transects,
        features,
        target_segment_length_m=config.coastline.target_segment_length_m,
        minimum_segment_length_m=config.coastline.minimum_segment_length_m,
        maximum_ambiguous_share=config.quality.maximum_ambiguous_orientation_share,
        maximum_missing_terrain_share=config.quality.maximum_missing_terrain_share,
    )
    qa_summary.update(
        {
            "region_id": config.region_id,
            "orientation_counts": {
                str(k): int(v) for k, v in segments.orientation_status.value_counts().items()
            },
            "terrain_quality_counts": {
                str(k): int(v) for k, v in features.terrain_quality_flag.value_counts().items()
            },
            "warnings": warnings,
            "terrain_cache_used": terrain.cache_used,
            "terrain_cache_key": terrain.cache_key,
        }
    )
    qa_summary_path = write_qa_summary(qa_summary, qa_dir / "qa_summary.json")
    output_paths.append(qa_summary_path)
    artifacts: list[Path] = []
    if not skip_qa_map:
        artifacts = generate_qa_artifacts(
            land.geometry,
            coastline,
            segments,
            transects,
            samples,
            features,
            terrain.dem_path,
            qa_dir,
            config.quality.random_qa_sample_size,
        )
        output_paths.extend(artifacts)
    counts = {
        "coastline_parts": len(coastline),
        "segments": len(segments),
        "transects": len(transects),
        "terrain_samples": len(samples),
        "terrain_feature_rows": len(features),
    }
    report_path = write_qa_report(
        report_dir / "phase1_qa_report.html",
        region_name=config.region_name,
        input_files=[_relative(land_path, root), _relative(dem_source_path, root)],
        source_warnings=warnings,
        counts=counts,
        coastline_length_m=float(coastline.length.sum()),
        orientation_counts=qa_summary["orientation_counts"],
        terrain_valid_share=float(features.terrain_valid_sample_share.mean()),
        qa_summary=qa_summary,
        artifacts=[_relative(path, root) for path in artifacts],
    )
    output_paths.append(report_path)
    completed = datetime.now(UTC)
    with rasterio.open(dem_source_path) as source_dem:
        input_crs = {"land_polygon": land.original_crs, "elevation": str(source_dem.crs)}
    manifest = RunManifest(
        run_id=run_id,
        region_id=config.region_id,
        pipeline_version=__version__,
        git_commit=git_commit(root),
        started_at_utc=started.isoformat(),
        completed_at_utc=completed.isoformat(),
        status="success" if qa_summary["passed"] else "quality_failed",
        configuration_path=_relative(config_path, root),
        configuration_checksum=config_checksum,
        input_files=[_relative(land_path, root), _relative(dem_source_path, root)],
        input_checksums={
            _relative(land_path, root): source_checksum,
            _relative(dem_source_path, root): dem_checksum,
        },
        input_crs=input_crs,
        input_resolutions={
            "elevation_source": list(terrain.source_resolution),
            "elevation_output": list(terrain.output_resolution),
        },
        output_files=[_relative(path, root) for path in output_paths],
        output_checksums={_relative(path, root): sha256_file(path) for path in output_paths},
        feature_counts=counts,
        warning_counts=dict(Counter(warning.split(":", 1)[0] for warning in warnings)),
        quality_results=qa_summary,
        software_versions=_software_versions(),
    )
    write_manifest(manifest, manifest_path)
    logging.getLogger("coastscan").info(
        "stage=complete status=%s segments=%d outputs=%d",
        manifest.status,
        len(segments),
        len(output_paths),
    )
    if not qa_summary["passed"]:
        raise QualityThresholdError(
            "Phase 1 outputs were generated but QA failed: "
            f"{', '.join(qa_summary['failed_checks'])}. "
            f"See {_relative(qa_summary_path, root)}"
        )
    return manifest
