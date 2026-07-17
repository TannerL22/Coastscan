"""Lightweight read-only diagnostics for the viewer geometry contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np

from coastscan.catalog.manifests import sha256_file
from coastscan.exceptions import ViewerError
from coastscan.viewer.data import (
    discover_viewer_paths,
    load_display_transects,
    load_viewer_data,
    viewer_project_root,
)
from coastscan.viewer.validation import geometry_columns


def _bounds(frame: gpd.GeoDataFrame) -> list[float]:
    return [float(value) for value in frame.total_bounds]


def _file_report(path: Path) -> dict[str, Any]:
    frame = gpd.read_parquet(path)
    if frame.crs is None:
        raise ViewerError(f"Viewer diagnostic found no CRS metadata: {path}")
    lengths = frame.geometry.length if frame.crs.is_projected else None
    result: dict[str, Any] = {
        "path": str(path),
        "sha256": sha256_file(path),
        "crs": frame.crs.to_string(),
        "active_geometry": frame.geometry.name,
        "geometry_columns": geometry_columns(frame),
        "geometry_types": {
            str(name): int(count) for name, count in frame.geometry.geom_type.value_counts().items()
        },
        "feature_count": len(frame),
        "empty_geometry_count": int((frame.geometry.isna() | frame.geometry.is_empty).sum()),
        "invalid_geometry_count": int((~frame.geometry.is_valid).sum()),
        "native_bounds": _bounds(frame),
        "wgs84_bounds": _bounds(frame.to_crs("EPSG:4326")),
        "duplicate_segment_id_count": (
            int(frame.segment_id.astype(str).duplicated().sum())
            if "segment_id" in frame.columns
            else None
        ),
    }
    if lengths is not None:
        result["length_distribution_m"] = {
            "minimum": float(lengths.min()),
            "median": float(lengths.quantile(0.5)),
            "p90": float(lengths.quantile(0.9)),
            "maximum": float(lengths.max()),
        }
    return result


def _geometry_comparison(
    authoritative: gpd.GeoDataFrame,
    attributes: gpd.GeoDataFrame,
) -> dict[str, Any]:
    reference_ids = set(authoritative.segment_id.astype(str))
    attribute_ids = set(attributes.segment_id.astype(str))
    common = sorted(reference_ids & attribute_ids)
    candidate = attributes.to_crs(authoritative.crs).copy()
    reference_by_id = authoritative.set_index(authoritative.segment_id.astype(str)).geometry
    candidate_by_id = candidate.set_index(candidate.segment_id.astype(str)).geometry
    exact = [
        reference_by_id[segment_id].equals_exact(candidate_by_id[segment_id], tolerance=0)
        for segment_id in common
    ]
    distances = [
        reference_by_id[segment_id].hausdorff_distance(candidate_by_id[segment_id])
        for segment_id in common
    ]
    return {
        "matching_segment_ids": len(common),
        "authoritative_segment_ids": len(reference_ids),
        "attribute_segment_ids": len(attribute_ids),
        "missing_attribute_ids": sorted(reference_ids - attribute_ids),
        "extra_attribute_ids": sorted(attribute_ids - reference_ids),
        "crs_matches": attributes.crs == authoritative.crs,
        "exact_geometry_matches": int(sum(exact)),
        "median_hausdorff_distance_m": float(np.median(distances)) if distances else None,
        "maximum_hausdorff_distance_m": float(max(distances)) if distances else None,
    }


def inspect_viewer_geometry(region_id: str, root: Path | None = None) -> dict[str, Any]:
    """Report the authoritative geometry, attribute join and render preconditions."""
    project_root = (root or viewer_project_root()).resolve()
    paths = discover_viewer_paths(region_id, project_root)
    attribute_path = (
        paths.preferred_segments
        if paths.preferred_segments.is_file()
        else paths.phase1_segments
        if paths.phase1_segments.is_file()
        else None
    )
    if attribute_path is None:
        raise ViewerError(f"No viewer attribute file exists for {region_id}.")
    if not paths.coast_segments.is_file():
        raise ViewerError(
            f"Authoritative viewer coastline geometry is missing: {paths.coast_segments}"
        )
    authoritative = gpd.read_parquet(paths.coast_segments)
    attributes = gpd.read_parquet(attribute_path)
    data = load_viewer_data(region_id, project_root)
    files = {
        "coast_segments": _file_report(paths.coast_segments),
        "attributes": _file_report(attribute_path),
    }
    transect_report: dict[str, Any] | None = None
    if paths.bathymetry_transects.is_file():
        files["bathymetry_transects"] = _file_report(paths.bathymetry_transects)
        transects = load_display_transects(data)
        transect_report = {
            "validated_feature_count": len(transects),
            "wgs84_bounds": _bounds(transects) if not transects.empty else None,
            "independent_path_ids": int(transects.bathymetry_transect_id.nunique()),
        }
    validation = data.geometry_validation.as_dict()
    return {
        "region_id": region_id,
        "authoritative_geometry_file": str(paths.coast_segments),
        "attribute_file": str(attribute_path),
        "geometry_checksum": data.geometry_checksum,
        "attribute_checksum": data.attribute_checksum,
        "files": files,
        "segment_id_agreement": _geometry_comparison(authoritative, attributes),
        "native_crs": data.source_crs,
        "native_bounds": validation["native_bounds"],
        "wgs84_bounds": validation["wgs84_bounds"],
        "aoi_bounds_wgs84": validation["aoi_bounds_wgs84"],
        "segment_count": validation["feature_count"],
        "geometry_types": validation["geometry_types"],
        "length_distribution_m": {
            "minimum": validation["length_min_m"],
            "median": validation["length_p50_m"],
            "p90": validation["length_p90_m"],
            "maximum": validation["length_max_m"],
        },
        "invalid_coordinate_count": validation["invalid_coordinate_count"],
        "out_of_aoi_count": validation["out_of_aoi_count"],
        "maximum_coordinate_jump_m": validation["maximum_coordinate_jump_m"],
        "transects": transect_report,
        "viewer_geometry_validation": "pass",
    }
