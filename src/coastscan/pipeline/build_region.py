"""End-to-end deterministic Phase 1 and Phase 1.5 regional build."""

import json
import logging
import math
import platform
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from shapely import line_merge
from shapely.geometry import Point, box
from shapely.ops import transform, unary_union

from coastscan import __version__
from coastscan.catalog.manifests import git_commit, sha256_file, sha256_text, write_manifest
from coastscan.catalog.sources import load_source_catalog, source_metadata_warnings
from coastscan.coastline.clean import clean_coastline
from coastscan.coastline.direct import DirectCoastlineResult, load_direct_coastline
from coastscan.coastline.extract import extract_coastline
from coastscan.coastline.orientation import orient_segments
from coastscan.coastline.segment import segment_coastline
from coastscan.coastline.transects import generate_transects
from coastscan.config import PROJECT_ROOT, data_path, load_region_config
from coastscan.exceptions import QualityThresholdError
from coastscan.io.outputs import write_geoparquet, write_parquet
from coastscan.io.rasters import (
    inspect_raster_tiles,
    prepare_terrain,
    resolve_raster_paths,
)
from coastscan.io.vectors import LandLoadResult, load_land
from coastscan.logging_config import configure_logging
from coastscan.models.manifests import RunManifest
from coastscan.models.region import RegionConfig
from coastscan.qa.checks import run_qa_checks
from coastscan.qa.maps import generate_qa_artifacts
from coastscan.qa.reports import write_qa_report, write_qa_summary
from coastscan.terrain.features import calculate_terrain_features
from coastscan.terrain.sampling import sample_terrain


@dataclass(frozen=True)
class SpatialInputs:
    aoi: Any
    land: LandLoadResult
    coastline: gpd.GeoDataFrame
    direct: DirectCoastlineResult | None
    coastline_source_path: Path
    coastline_source_checksum: str
    land_path: Path
    aoi_path: Path | None


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


def _load_spatial_inputs(config: RegionConfig, root: Path) -> SpatialInputs:
    land_config = config.inputs.land_polygon
    assert land_config is not None
    aoi_path: Path | None = None
    aoi = None
    if config.area_of_interest is not None:
        aoi_path = data_path(config.area_of_interest.path, root)
        aoi_result = load_land(
            aoi_path,
            config.area_of_interest.layer,
            config.analysis_crs,
        )
        aoi = aoi_result.geometry
    land_path = data_path(land_config.path, root)
    clip_buffer = (
        max(
            [
                config.transects.inland_length_m,
                config.transects.offshore_length_m,
                config.coastline.orientation_test_distance_m,
                *config.coastline.orientation_fallback_distances_m,
            ]
        )
        + 25.0
    )
    land = load_land(
        land_path,
        land_config.layer,
        config.analysis_crs,
        selection_filters=land_config.selection_filters,
        clip_geometry=aoi,
        clip_buffer_m=clip_buffer,
    )
    if aoi is None:
        aoi = land.geometry
    direct_config = config.inputs.coastline
    if direct_config is None:
        checksum = sha256_file(land_path)
        relevant = json.dumps(config.coastline.model_dump(), sort_keys=True)
        coastline_version = sha256_text(f"{config.region_id}:{checksum}:{relevant}")[:12]
        coastline = extract_coastline(
            land.geometry,
            region_id=config.region_id,
            source_id=land_config.source_id,
            source_checksum=checksum,
            processing_version=coastline_version,
            crs=config.analysis_crs,
            include_interior=config.coastline.include_interior_shorelines,
        )
        return SpatialInputs(
            aoi=aoi,
            land=land,
            coastline=coastline,
            direct=None,
            coastline_source_path=land_path,
            coastline_source_checksum=checksum,
            land_path=land_path,
            aoi_path=aoi_path,
        )
    coast_path = data_path(direct_config.path, root)
    checksum = sha256_file(coast_path) if coast_path.is_file() else "missing"
    relevant = json.dumps(
        {
            "coastline": config.coastline.model_dump(),
            "input": direct_config.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    coastline_version = sha256_text(f"{config.region_id}:{checksum}:{relevant}")[:12]
    direct = load_direct_coastline(
        coast_path,
        layer=direct_config.layer,
        analysis_crs=config.analysis_crs,
        aoi=aoi,
        region_id=config.region_id,
        source_id=direct_config.source_id,
        source_checksum=checksum,
        processing_version=coastline_version,
        feature_filters=direct_config.feature_filters,
        source_id_field=direct_config.source_id_field,
        source_class_field=direct_config.source_class_field,
        duplicate_tolerance_m=direct_config.duplicate_tolerance_m,
    )
    return SpatialInputs(
        aoi=aoi,
        land=land,
        coastline=direct.coastline,
        direct=direct,
        coastline_source_path=coast_path,
        coastline_source_checksum=checksum,
        land_path=land_path,
        aoi_path=aoi_path,
    )


def _distance_summary(values: Any) -> dict[str, float | None]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not array.size:
        return {"minimum": None, "p50": None, "p90": None, "maximum": None}
    return {
        "minimum": float(np.min(array)),
        "p50": float(np.percentile(array, 50)),
        "p90": float(np.percentile(array, 90)),
        "maximum": float(np.max(array)),
    }


def _distribution_summary(values: Any) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not array.size:
        return {
            "count": 0,
            "minimum": None,
            "p10": None,
            "p50": None,
            "p90": None,
            "maximum": None,
            "mean": None,
        }
    return {
        "count": int(array.size),
        "minimum": float(np.min(array)),
        "p10": float(np.percentile(array, 10)),
        "p50": float(np.percentile(array, 50)),
        "p90": float(np.percentile(array, 90)),
        "maximum": float(np.max(array)),
        "mean": float(np.mean(array)),
    }


def _coastline_components(coastline: gpd.GeoDataFrame) -> int:
    merged = line_merge(coastline.geometry.union_all())
    return len(merged.geoms) if hasattr(merged, "geoms") else 1


def _endpoint_failure_counts(transects: gpd.GeoDataFrame, land: Any) -> dict[str, int]:
    inland = transects.loc[transects.direction == "inland"]
    offshore = transects.loc[transects.direction == "offshore"]
    inland_failures = int(
        (~inland.geometry.map(lambda line: land.covers(Point(line.coords[-1])))).sum()
    )
    offshore_failures = int(
        offshore.geometry.map(lambda line: land.covers(Point(line.coords[-1]))).sum()
    )
    return {
        "inland_endpoints_not_on_land": inland_failures,
        "offshore_endpoints_on_land": offshore_failures,
    }


def _source_class_review(direct: DirectCoastlineResult | None) -> dict[str, Any] | None:
    if direct is None:
        return None
    audit = direct.audit
    selected = audit.loc[audit.selected_for_analysis]
    rejected = audit.loc[~audit.selected_for_analysis]
    suspicious_tokens = ("MUELLE", "ROMPEOLAS", "SLCONS", "VARADERO", "RIVER")
    suspicious = selected.loc[
        selected.source_class.astype(str)
        .str.upper()
        .map(lambda value: any(token in value for token in suspicious_tokens))
    ]
    return {
        "selected_class_counts": {
            str(key): int(value) for key, value in selected.source_class.value_counts().items()
        },
        "rejected_class_counts": {
            str(key): int(value) for key, value in rejected.source_class.value_counts().items()
        },
        "selected_potential_harbour_artificial_or_river_count": len(suspicious),
        "selected_potential_harbour_artificial_or_river_classes": sorted(
            suspicious.source_class.astype(str).unique().tolist()
        ),
        "interpretation": (
            "Counts flag source classifications for review; they are not automatically errors "
            "because official high-water closure lines can legitimately include structures."
        ),
    }


def _extreme_segments(features: Any, field: str, count: int = 5) -> list[dict[str, Any]]:
    if field not in features.columns:
        return []
    selected = features.loc[features[field].notna(), ["segment_id", field]].nlargest(count, field)
    return [
        {"segment_id": str(row.segment_id), "value": float(getattr(row, field))}
        for row in selected.itertuples(index=False)
    ]


def _tile_edge_review(
    segments: gpd.GeoDataFrame,
    features: Any,
    tile_paths: tuple[Path, ...],
    analysis_crs: str,
    resolution: tuple[float, float],
) -> dict[str, Any]:
    boundaries = []
    for path in tile_paths:
        with rasterio.open(path) as dataset:
            geometry = box(*dataset.bounds)
            if str(dataset.crs) != analysis_crs:
                transformer = Transformer.from_crs(dataset.crs, analysis_crs, always_xy=True)
                geometry = transform(transformer.transform, geometry)
            boundaries.append(geometry.boundary)
    edge_geometry = unary_union(boundaries)
    tolerance = max(resolution) * 1.5
    near = segments.loc[segments.geometry.distance(edge_geometry) <= tolerance]
    near_features = features.loc[features.segment_id.isin(near.segment_id)]
    low_completeness = int((near_features.terrain_valid_sample_share < 0.95).sum())
    implausible_slopes = int(
        ((near_features.slope_max_deg < 0) | (near_features.slope_max_deg > 90)).sum()
    )
    return {
        "proximity_tolerance_m": tolerance,
        "segments_near_source_tile_edges": len(near),
        "near_edge_segments_below_95pct_completeness": low_completeness,
        "near_edge_segments_with_slope_outside_0_90": implausible_slopes,
        "automated_artifact_flag": bool(low_completeness or implausible_slopes),
        "note": "This is a screening metric; the QA maps still require visual review.",
    }


def _source_warnings(config: RegionConfig, root: Path) -> list[str]:
    catalog = load_source_catalog(root / "data_catalog" / "sources.csv")
    source_ids = [config.inputs.elevation.source_id]
    if config.inputs.land_polygon is not None:
        source_ids.append(config.inputs.land_polygon.source_id)
    if config.inputs.coastline is not None:
        source_ids.append(config.inputs.coastline.source_id)
    warnings: list[str] = []
    for source_id in source_ids:
        warnings.extend(
            f"{source_id}: {warning}"
            for warning in source_metadata_warnings(catalog.get(source_id))
        )
    return warnings


def inspect_region_inputs(region: str | Path, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Inspect direct/derived vector inputs and tile headers before expensive processing."""
    config, config_path = load_region_config(region, root)
    spatial = _load_spatial_inputs(config, root)
    raster_paths = resolve_raster_paths(config.inputs.elevation, root)
    terrain_buffer = (
        config.transects.inland_length_m
        + config.terrain.roughness_window_m
        + config.terrain.origin_search_max_distance_m
        + 10
    )
    processing_corridor = spatial.coastline.geometry.union_all().buffer(terrain_buffer)
    available_tiles, selected_tiles = inspect_raster_tiles(
        raster_paths,
        processing_corridor,
        config.analysis_crs,
        config.inputs.elevation.vertical_units,
    )
    resolution = selected_tiles[0].resolution
    bounds = processing_corridor.bounds
    estimated_width = math.ceil((bounds[2] - bounds[0]) / resolution[0])
    estimated_height = math.ceil((bounds[3] - bounds[1]) / resolution[1])
    boundary_distances = spatial.coastline.geometry.map(
        lambda geometry: geometry.distance(spatial.land.geometry.boundary)
    )
    direct_info: dict[str, Any] | None = None
    if spatial.direct is not None:
        direct = spatial.direct
        direct_config = config.inputs.coastline
        assert direct_config is not None
        classes = sorted(direct.audit.source_class.dropna().astype(str).unique().tolist())
        direct_info = {
            "mode": "direct",
            "path": _relative(spatial.coastline_source_path, root),
            "source_feature_count": direct.stats.source_feature_count,
            "source_geometry_types": direct.audit.geom_type.value_counts().to_dict(),
            "available_fields": list(direct.stats.available_fields),
            "available_class_values": classes,
            "configured_filters": [
                predicate.model_dump(mode="json") for predicate in direct_config.feature_filters
            ],
            "original_crs": direct.stats.original_crs,
            "feature_count_after_filtering": direct.stats.selected_feature_count,
            "feature_count_after_aoi_clipping": direct.stats.clipped_feature_count,
            "total_clipped_coastline_length_m": direct.stats.selected_length_m,
            "suspected_duplicate_count": direct.stats.suspected_duplicate_count,
        }
    else:
        direct_info = {
            "mode": "polygon_derived",
            "feature_count_after_aoi_clipping": len(spatial.coastline),
            "total_clipped_coastline_length_m": float(spatial.coastline.length.sum()),
        }
    selected_record_fields = [
        field
        for field in ("INSPIREID", "NATCODE", "NAMEUNIT")
        if field in spatial.land.selected_records.columns
    ]
    land_info = {
        "path": _relative(spatial.land_path, root),
        "feature_count": spatial.land.feature_count,
        "source_feature_count": spatial.land.source_feature_count,
        "selected_feature_count": len(spatial.land.selected_records),
        "selected_records": spatial.land.selected_records[selected_record_fields].to_dict(
            orient="records"
        ),
        "original_crs": spatial.land.original_crs,
        "analysis_crs": config.analysis_crs,
        "originally_valid": spatial.land.originally_valid,
        "repair_applied": spatial.land.repair_applied,
        "area_m2": spatial.land.final_area_m2,
        "geometry_type": spatial.land.geometry.geom_type,
        "overlaps_coastline": bool(spatial.land.geometry.intersects(spatial.coastline.union_all())),
        "coastline_boundary_distance_m": _distance_summary(boundary_distances),
    }
    return {
        "region_id": config.region_id,
        "configuration": _relative(config_path, root),
        "coastline": direct_info,
        "land": land_info,
        "land_mask": land_info,
        "elevation": {
            "available_tile_count": len(available_tiles),
            "intersecting_tile_count": len(selected_tiles),
            "selected_paths": [_relative(info.path, root) for info in selected_tiles],
            "crs": selected_tiles[0].crs,
            "resolution": list(resolution),
            "nodata_values": sorted({str(info.nodata) for info in selected_tiles}),
            "vertical_units": sorted({info.vertical_units for info in selected_tiles}),
            "selected_file_size_bytes": sum(info.file_size_bytes for info in selected_tiles),
            "estimated_clipped_dimensions": [estimated_width, estimated_height],
            "estimated_clipped_uncompressed_bytes": estimated_width * estimated_height * 4,
            "mosaic_descriptor_exists": (
                root / "data" / "interim" / config.region_id / "dem_mosaic.vrt.json"
            ).is_file(),
            "cache_exists": (
                root / "data" / "interim" / config.region_id / "terrain_cache.json"
            ).is_file(),
        },
        "source_warnings": _source_warnings(config, root),
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
    """Execute all terrestrial Phase 1 stages for derived or direct coastline mode."""
    config, config_path = load_region_config(region, root)
    started = datetime.now(UTC)
    run_started_perf = time.perf_counter()
    stage_started = run_started_perf
    stage_runtimes: dict[str, float] = {}

    def complete_stage(name: str) -> None:
        nonlocal stage_started
        now = time.perf_counter()
        stage_runtimes[name] = now - stage_started
        stage_started = now

    run_id = f"{started.strftime('%Y%m%dT%H%M%S%fZ')}_{config.region_id}"
    manifest_dir = root / "outputs" / "manifests" / config.region_id
    manifest_path = manifest_dir / f"{run_id}.json"
    log_path = manifest_dir / f"{run_id}.log"
    logger = configure_logging(log_path, verbose)
    logger.info("stage=input loading region=%s", config.region_id)
    spatial = _load_spatial_inputs(config, root)
    raster_paths = resolve_raster_paths(config.inputs.elevation, root)
    warnings = _source_warnings(config, root)
    if spatial.land.repair_applied:
        warnings.append("Land geometry was repaired with shapely.make_valid")
    complete_stage("input_loading")

    interim = root / "data" / "interim" / config.region_id
    processed = root / "data" / "processed" / config.region_id
    qa_dir = root / "outputs" / "qa" / config.region_id
    report_dir = root / "outputs" / "reports" / config.region_id
    output_paths: list[Path] = []
    coastline, cleaning = clean_coastline(
        spatial.coastline, config.coastline.simplification_tolerance_m
    )
    if cleaning.warning:
        warnings.append(cleaning.warning)
    output_paths.append(write_geoparquet(coastline, interim / "coastline_clean.parquet"))
    if spatial.direct is not None:
        output_paths.append(
            write_geoparquet(
                spatial.direct.audit,
                interim / "coastline_source_audit.parquet",
            )
        )
        output_paths.append(
            write_geoparquet(
                spatial.land.selected_records,
                interim / "land_mask_source_audit.parquet",
            )
        )
    complete_stage("coastline_preparation")

    logger.info("stage=segmentation coastline_parts=%d", len(coastline))
    coastline_version = str(coastline.processing_version.iloc[0])
    segments = segment_coastline(
        coastline,
        region_id=config.region_id,
        coastline_version=coastline_version,
        target_length_m=config.coastline.target_segment_length_m,
        minimum_length_m=config.coastline.minimum_segment_length_m,
    )
    segments = orient_segments(
        segments,
        spatial.land.geometry,
        config.coastline.orientation_test_distance_m,
        config.coastline.orientation_fallback_distances_m,
        config.coastline.orientation_vote_offsets_m,
        config.coastline.source_mismatch_tolerance_m,
    )
    transects = generate_transects(
        segments,
        config.transects.spacing_m,
        config.transects.inland_length_m,
        config.transects.offshore_length_m,
    )
    output_paths.append(write_geoparquet(segments, processed / "coast_segments.parquet"))
    output_paths.append(write_geoparquet(transects, processed / "transects.parquet"))
    complete_stage("segmentation_orientation_transects")

    logger.info("stage=terrain preparing raster tiles=%d", len(raster_paths))
    terrain_buffer = (
        config.transects.inland_length_m
        + config.terrain.roughness_window_m
        + config.terrain.origin_search_max_distance_m
        + 10
    )
    processing_corridor = coastline.geometry.union_all().buffer(terrain_buffer)
    terrain = prepare_terrain(
        raster_paths,
        processing_corridor,
        config.analysis_crs,
        interim,
        config.terrain.roughness_window_m,
        vertical_units=config.inputs.elevation.vertical_units,
        force=force,
    )
    logger.info(
        "stage=terrain cache_used=%s cache_key=%s selected_tiles=%d clipped=%sx%s",
        terrain.cache_used,
        terrain.cache_key,
        terrain.selected_tile_count,
        terrain.clipped_dimensions[0],
        terrain.clipped_dimensions[1],
    )
    output_paths.extend(
        [
            terrain.dem_path,
            terrain.slope_path,
            terrain.roughness_path,
            terrain.mosaic_descriptor_path,
        ]
    )
    complete_stage("terrain_preparation")

    samples = sample_terrain(
        transects,
        terrain.dem_path,
        terrain.slope_path,
        terrain.roughness_path,
        config.terrain.sample_spacing_m,
        config.terrain.origin_search_max_distance_m,
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
    output_paths.append(write_parquet(features, processed / "terrain_features.parquet"))
    segment_features = gpd.GeoDataFrame(
        segments.merge(features, on="segment_id", how="left"),
        geometry="geometry",
        crs=segments.crs,
    )
    output_paths.append(write_geoparquet(segment_features, processed / "segment_features.parquet"))
    complete_stage("terrain_sampling_features")

    qa_summary = run_qa_checks(
        spatial.land.geometry,
        coastline,
        segments,
        transects,
        features,
        target_segment_length_m=config.coastline.target_segment_length_m,
        minimum_segment_length_m=config.coastline.minimum_segment_length_m,
        maximum_ambiguous_share=config.quality.maximum_ambiguous_orientation_share,
        maximum_missing_terrain_share=config.quality.maximum_missing_terrain_share,
    )
    orientation_counts = {
        str(key): int(value) for key, value in segments.orientation_status.value_counts().items()
    }
    origin_counts = {
        str(key): int(value) for key, value in features.terrain_origin_method.value_counts().items()
    }
    direct_stats = spatial.direct.stats.__dict__ if spatial.direct is not None else None
    endpoint_failures = _endpoint_failure_counts(transects, spatial.land.geometry)
    connected_components = _coastline_components(coastline)
    relief_columns = [column for column in features.columns if column.startswith("land_relief_")]
    relief_distributions = {
        column: _distribution_summary(features[column]) for column in relief_columns
    }
    terrain_total_transects = int(features.terrain_transect_count.sum())
    terrain_valid_transects = int(features.terrain_valid_transect_count.sum())
    origin_samples = samples.drop_duplicates("transect_id")
    mismatch_ids = set(
        segments.loc[segments.orientation_source_mismatch_flag, "segment_id"].astype(str)
    )
    mismatched_features = features.loc[features.segment_id.astype(str).isin(mismatch_ids)]
    matched_features = features.loc[~features.segment_id.astype(str).isin(mismatch_ids)]
    orientation_mismatch_table = {
        f"{row.orientation_status}|mismatch={bool(row.orientation_source_mismatch_flag)}": int(
            row.count
        )
        for row in (
            segments.groupby(
                ["orientation_status", "orientation_source_mismatch_flag"], dropna=False
            )
            .size()
            .rename("count")
            .reset_index()
            .itertuples(index=False)
        )
    }
    zero_relief_field = "land_relief_50m_p50_m"
    zero_relief_count = (
        int((features[zero_relief_field].abs() <= 0.05).sum())
        if zero_relief_field in features
        else 0
    )
    qa_summary.update(
        {
            "region_id": config.region_id,
            "coastline_mode": "direct" if spatial.direct is not None else "polygon_derived",
            "coastline_source": direct_stats,
            "cleaning": cleaning.__dict__,
            "coastline_qa": {
                "pilot_length_m": float(coastline.length.sum()),
                "source_record_count": (
                    direct_stats["source_feature_count"]
                    if direct_stats is not None
                    else len(coastline)
                ),
                "cleaned_part_count": len(coastline),
                "segment_count": len(segments),
                "segment_length_m": _distribution_summary(segments.segment_length_m),
                "cleaning_length_change_percent": cleaning.length_change_percent,
                "connected_component_count": connected_components,
                "apparent_gap_or_disconnected_fragment_count": max(0, connected_components - 1),
                "tiny_fragment_count_below_minimum_segment_length": int(
                    (coastline.length < config.coastline.minimum_segment_length_m).sum()
                ),
                "suspected_duplicate_shoreline_count": (
                    direct_stats["suspected_duplicate_count"] if direct_stats is not None else 0
                ),
                "source_class_review": _source_class_review(spatial.direct),
                "gap_interpretation": (
                    "Disconnected components include legitimate islands and AOI-edge clipping; "
                    "they are reported for visual review, not automatically treated as defects."
                ),
            },
            "orientation_counts": orientation_counts,
            "orientation_method_counts": {
                str(key): int(value)
                for key, value in segments.orientation_method.value_counts().items()
            },
            "orientation_source_mismatch_count": int(
                segments.orientation_source_mismatch_flag.sum()
            ),
            "orientation_endpoint_failures": endpoint_failures,
            "coast_to_landmask_boundary_distance_m": _distance_summary(
                segments.coast_to_landmask_boundary_distance_m
            ),
            "cross_source_qa": {
                "coastline_to_landmask_boundary_distance_m": _distance_summary(
                    segments.coast_to_landmask_boundary_distance_m
                ),
                "coastline_to_nearest_valid_inland_dem_sample_m": {
                    **_distance_summary(origin_samples.terrain_origin_shift_m),
                    "unavailable_origin_count": int(
                        (origin_samples.terrain_origin_method == "unavailable").sum()
                    ),
                    "method": (
                        "Distance along each inland transect from its coastline origin to the "
                        "first valid DEM sample; this does not imply underwater coverage."
                    ),
                },
                "orientation_status_by_source_mismatch": orientation_mismatch_table,
                "mean_terrain_completeness_source_mismatch": (
                    float(mismatched_features.terrain_valid_sample_share.mean())
                    if len(mismatched_features)
                    else None
                ),
                "mean_terrain_completeness_no_source_mismatch": (
                    float(matched_features.terrain_valid_sample_share.mean())
                    if len(matched_features)
                    else None
                ),
                "suspected_coastline_classification": _source_class_review(spatial.direct),
            },
            "terrain_quality_counts": {
                str(key): int(value)
                for key, value in features.terrain_quality_flag.value_counts().items()
            },
            "terrain_origin_method_counts": origin_counts,
            "terrain_origin_elevation_m": _distance_summary(features.terrain_origin_elevation_m),
            "terrain_origin_shift_m": _distance_summary(features.terrain_origin_shift_m),
            "negative_terrain_origin_count": int((features.terrain_origin_elevation_m < 0).sum()),
            "terrain_qa": {
                "valid_transect_count": terrain_valid_transects,
                "total_transect_count": terrain_total_transects,
                "valid_transect_share": (
                    terrain_valid_transects / terrain_total_transects
                    if terrain_total_transects
                    else 0.0
                ),
                "valid_sample_share": (
                    int(features.terrain_valid_sample_count.sum())
                    / int(features.terrain_sample_count.sum())
                    if int(features.terrain_sample_count.sum())
                    else 0.0
                ),
                "segments_outside_dem": int((features.terrain_quality_flag == "outside_dem").sum()),
                "segments_partial_dem": int(
                    features.terrain_quality_flag.isin(["partial", "insufficient"]).sum()
                ),
                "relief_distributions_m": relief_distributions,
                "slope_p50_deg": _distribution_summary(features.slope_p50_deg),
                "slope_p90_deg": _distribution_summary(features.slope_p90_deg),
                "slope_max_deg": _distribution_summary(features.slope_max_deg),
                "roughness_p50": _distribution_summary(features.roughness_p50),
                "roughness_p90": _distribution_summary(features.roughness_p90),
                "extreme_segments": {
                    "slope_max_deg": _extreme_segments(features, "slope_max_deg"),
                    "roughness_p90": _extreme_segments(features, "roughness_p90"),
                    "land_relief_100m_p90_m": _extreme_segments(features, "land_relief_100m_p90_m"),
                },
                "implausible_slope_segment_count": int(
                    ((features.slope_max_deg < 0) | (features.slope_max_deg > 90)).sum()
                ),
                "suspicious_zero_relief_50m_segment_count": zero_relief_count,
                "tile_edge_review": _tile_edge_review(
                    segments,
                    features,
                    terrain.selected_paths,
                    config.analysis_crs,
                    terrain.output_resolution,
                ),
            },
            "warnings": warnings,
            "terrain_cache_used": terrain.cache_used,
            "terrain_cache_key": terrain.cache_key,
            "terrain_tiles": {
                "available": terrain.available_tile_count,
                "selected": terrain.selected_tile_count,
                "paths": [_relative(path, root) for path in terrain.selected_paths],
                "selected_file_size_bytes": terrain.selected_file_size_bytes,
                "selected_uncompressed_bytes": terrain.selected_uncompressed_bytes,
                "clipped_dimensions": list(terrain.clipped_dimensions),
                "clipped_uncompressed_bytes": terrain.clipped_uncompressed_bytes,
                "cache_creation_seconds": terrain.cache_creation_seconds,
                "peak_memory_bytes": None,
                "peak_memory_note": "Not measured reliably; window size bounds array allocation.",
            },
            "stage_runtimes_seconds": stage_runtimes,
        }
    )
    qa_summary_path = write_qa_summary(qa_summary, qa_dir / "qa_summary.json")
    output_paths.append(qa_summary_path)
    artifacts: list[Path] = []
    if not skip_qa_map:
        artifacts = generate_qa_artifacts(
            spatial.land.geometry,
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
    input_paths = [spatial.land_path, *terrain.selected_paths]
    if spatial.coastline_source_path != spatial.land_path:
        input_paths.insert(0, spatial.coastline_source_path)
    if spatial.aoi_path is not None:
        input_paths.insert(0, spatial.aoi_path)
    report_path = write_qa_report(
        report_dir / "phase1_qa_report.html",
        region_name=config.region_name,
        input_files=[_relative(path, root) for path in input_paths],
        source_warnings=warnings,
        counts=counts,
        coastline_length_m=float(coastline.length.sum()),
        orientation_counts=orientation_counts,
        terrain_valid_share=float(features.terrain_valid_sample_share.mean()),
        qa_summary=qa_summary,
        artifacts=[_relative(path, root) for path in artifacts],
    )
    output_paths.append(report_path)
    complete_stage("qa_and_reporting")
    qa_summary["stage_runtimes_seconds"] = stage_runtimes
    qa_summary["total_runtime_seconds"] = time.perf_counter() - run_started_perf
    write_qa_summary(qa_summary, qa_summary_path)

    completed = datetime.now(UTC)
    input_checksums = {_relative(path, root): sha256_file(path) for path in input_paths}
    input_crs = {
        "land_polygon": spatial.land.original_crs,
        "elevation": terrain.source_crs,
    }
    if spatial.direct is not None:
        input_crs["coastline"] = spatial.direct.stats.original_crs
    manifest = RunManifest(
        run_id=run_id,
        region_id=config.region_id,
        pipeline_version=__version__,
        git_commit=git_commit(root),
        started_at_utc=started.isoformat(),
        completed_at_utc=completed.isoformat(),
        status="success" if qa_summary["passed"] else "quality_failed",
        configuration_path=_relative(config_path, root),
        configuration_checksum=sha256_file(config_path),
        input_files=[_relative(path, root) for path in input_paths],
        input_checksums=input_checksums,
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
        "stage=complete status=%s segments=%d outputs=%d runtime=%.3fs",
        manifest.status,
        len(segments),
        len(output_paths),
        qa_summary["total_runtime_seconds"],
    )
    if not qa_summary["passed"]:
        raise QualityThresholdError(
            "Phase 1 outputs were generated but QA failed: "
            f"{', '.join(qa_summary['failed_checks'])}. "
            f"See {_relative(qa_summary_path, root)}"
        )
    return manifest
