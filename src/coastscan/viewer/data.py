"""Immutable discovery, validation and cached display reprojection."""

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import pandas as pd
import streamlit as st

from coastscan.catalog.manifests import sha256_file
from coastscan.config import PROJECT_ROOT, data_path, load_region_config
from coastscan.exceptions import ConfigurationError, ViewerError
from coastscan.viewer.models import ViewerData, ViewerPaths
from coastscan.viewer.validation import (
    GeometryValidationResult,
    geometry_columns,
    load_aoi,
    validate_line_geometry,
    validate_transect_geometry,
)

DISPLAY_CRS = "EPSG:4326"


def viewer_project_root() -> Path:
    """Use the repository convention, with a narrow test/development override."""
    override = os.environ.get("COASTSCAN_VIEWER_ROOT")
    return Path(override).resolve() if override else PROJECT_ROOT


def discover_viewer_paths(region_id: str, root: Path | None = None) -> ViewerPaths:
    project_root = (root or viewer_project_root()).resolve()
    processed = project_root / "data" / "processed" / region_id
    return ViewerPaths(
        region_id=region_id,
        phase3_segments=processed / "segment_features_phase3.parquet",
        preferred_segments=processed / "segment_features_phase2.parquet",
        phase1_segments=processed / "segment_features.parquet",
        coast_segments=processed / "coast_segments.parquet",
        bathymetry_features=processed / "bathymetry_features.parquet",
        bathymetry_transects=processed / "bathymetry_transects.parquet",
        clarity_seasonal_features=processed / "clarity_seasonal_features.parquet",
        manifest_directory=project_root / "outputs" / "manifests" / region_id,
    )


def missing_outputs_message(region_id: str) -> str:
    return f"""No processed CoastScan outputs were found for:
{region_id}

Run:

uv run coastscan acquire-region-data --region {region_id}
uv run coastscan build-region --region {region_id} --write-samples
uv run coastscan build-bathymetry --region {region_id} --write-samples"""


def _latest_manifests(directory: Path) -> dict[str, dict[str, object]]:
    if not directory.is_dir():
        return {}
    result: dict[str, dict[str, object]] = {}
    phase1 = sorted(
        path
        for path in directory.glob("*.json")
        if "_bathymetry" not in path.name and "optical" not in path.name
    )
    phase2 = sorted(directory.glob("*_bathymetry.json"))
    phase3 = sorted(directory.glob("*optical*.json"))
    for stage, candidates in (("phase1", phase1), ("phase2", phase2), ("phase3", phase3)):
        if not candidates:
            continue
        try:
            parsed = json.loads(candidates[-1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            result[stage] = parsed
    return result


def _signature(path: Path) -> tuple[str, int, int, str]:
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, sha256_file(path))


@st.cache_data(show_spinner=False)
def _load_segments_cached(
    geometry_path_text: str,
    geometry_signature: tuple[str, int, int, str],
    attribute_path_text: str,
    attribute_signature: tuple[str, int, int, str],
    aoi_path_text: str | None,
    aoi_signature: tuple[str, int, int, str] | None,
    aoi_layer: str | None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, str, GeometryValidationResult]:
    del geometry_signature, attribute_signature, aoi_signature
    geometry_path = Path(geometry_path_text)
    attribute_path = Path(attribute_path_text)
    authoritative = gpd.read_parquet(geometry_path)
    try:
        attributes: pd.DataFrame = gpd.read_parquet(attribute_path)
    except ValueError:
        attributes = pd.read_parquet(attribute_path)
    if "segment_id" not in attributes.columns:
        raise ViewerError(f"Viewer attribute file has no segment_id column: {attribute_path}")
    if attributes.empty:
        raise ViewerError(f"Viewer attribute file contains no rows: {attribute_path}")
    attribute_ids = attributes.segment_id.astype(str)
    duplicates = attribute_ids[attribute_ids.duplicated()].unique()
    if len(duplicates):
        raise ViewerError(
            "Viewer attribute file contains duplicate segment IDs: " + ", ".join(duplicates[:5])
        )

    if "segment_id" not in authoritative.columns:
        raise ViewerError(
            f"Authoritative coastline geometry has no segment_id column: {geometry_path}"
        )
    authoritative_ids = authoritative.segment_id.astype(str)
    geometry_duplicates = authoritative_ids[authoritative_ids.duplicated()].unique()
    if len(geometry_duplicates):
        raise ViewerError(
            "Authoritative coastline geometry contains duplicate segment IDs: "
            + ", ".join(geometry_duplicates[:5])
        )
    geometry_set = set(authoritative_ids)
    attribute_set = set(attribute_ids)
    missing = sorted(geometry_set - attribute_set)
    extra = sorted(attribute_set - geometry_set)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing attributes for {len(missing)} coastline segments")
        if extra:
            details.append(f"{len(extra)} attribute rows have no coastline segment")
        raise ViewerError(
            "Viewer geometry/attribute segment-ID mismatch: " + "; ".join(details) + "."
        )

    active_geometry = authoritative.geometry.name
    geometry_table = authoritative[["segment_id", active_geometry]].copy()
    geometry_table["segment_id"] = authoritative_ids
    drop_columns = geometry_columns(attributes)
    attribute_table = pd.DataFrame(attributes.drop(columns=drop_columns)).copy()
    attribute_table["segment_id"] = attribute_ids
    joined = geometry_table.merge(
        attribute_table,
        on="segment_id",
        how="left",
        sort=False,
        validate="one_to_one",
    )
    analytical = gpd.GeoDataFrame(joined, geometry=active_geometry, crs=authoritative.crs)
    aoi = load_aoi(Path(aoi_path_text), aoi_layer) if aoi_path_text else None
    display, validation = validate_line_geometry(
        analytical,
        label=f"Authoritative viewer coastline {geometry_path}",
        aoi=aoi,
    )
    source_crs = analytical.crs.to_string()
    return analytical, display, source_crs, validation


def load_viewer_data(region_id: str, root: Path | None = None) -> ViewerData:
    """Load the preferred joined output or fall back explicitly to terrain-only mode."""
    paths = discover_viewer_paths(region_id, root)
    mode: Literal["phase3", "phase2", "terrain_only"]
    if paths.phase3_segments.is_file():
        selected = paths.phase3_segments
        mode = "phase3"
    elif paths.preferred_segments.is_file():
        selected = paths.preferred_segments
        mode = "phase2"
    elif paths.phase1_segments.is_file():
        selected = paths.phase1_segments
        mode = "terrain_only"
    else:
        raise ViewerError(missing_outputs_message(region_id))
    if not paths.coast_segments.is_file():
        raise ViewerError(
            "Authoritative viewer coastline geometry is missing: "
            f"{paths.coast_segments}. Re-run the Phase 1 build for {region_id}."
        )
    project_root = (root or viewer_project_root()).resolve()
    geometry_signature = _signature(paths.coast_segments)
    attribute_signature = _signature(selected)
    coastline_source_id: str | None = None
    aoi_path: Path | None = None
    aoi_layer: str | None = None
    maximum_bathymetry_transect_length_m = 5_000.0
    try:
        config, _ = load_region_config(region_id, project_root)
        coastline_source_id = (
            config.inputs.coastline.source_id
            if config.inputs.coastline is not None
            else config.inputs.land_polygon.source_id
            if config.inputs.land_polygon is not None
            else None
        )
        if config.area_of_interest is not None:
            aoi_path = data_path(config.area_of_interest.path, project_root).resolve()
            aoi_layer = config.area_of_interest.layer
        if config.bathymetry is not None:
            maximum_bathymetry_transect_length_m = config.bathymetry.maximum_offshore_distance_m
    except ConfigurationError:
        coastline_source_id = None
    aoi_signature = _signature(aoi_path) if aoi_path is not None and aoi_path.is_file() else None
    analytical, display, source_crs, validation = _load_segments_cached(
        str(paths.coast_segments),
        geometry_signature,
        str(selected),
        attribute_signature,
        str(aoi_path) if aoi_path is not None else None,
        aoi_signature,
        aoi_layer,
    )
    return ViewerData(
        region_id=region_id,
        mode=mode,
        analytical_segments=analytical,
        display_segments=display,
        paths=paths,
        source_crs=source_crs,
        segment_checksum=attribute_signature[3],
        geometry_checksum=geometry_signature[3],
        attribute_checksum=attribute_signature[3],
        geometry_source=paths.coast_segments,
        attribute_source=selected,
        geometry_validation=validation,
        maximum_bathymetry_transect_length_m=maximum_bathymetry_transect_length_m,
        coastline_source_id=coastline_source_id,
        manifests=_latest_manifests(paths.manifest_directory),
    )


@st.cache_data(show_spinner=False)
def _load_seasonal_cached(path_text: str, signature: tuple[str, int, int, str]) -> pd.DataFrame:
    del signature
    return pd.read_parquet(path_text)


def with_optical_period(data: ViewerData, period_id: str) -> ViewerData:
    """Return a display-only period join while retaining coastline geometry authority."""
    path = data.paths.clarity_seasonal_features
    if not data.has_optical or not path.is_file():
        return data
    seasonal = _load_seasonal_cached(str(path), _signature(path))
    chosen = seasonal.loc[
        (seasonal.period_id.astype(str) == period_id)
        & (seasonal.zone_type.astype(str) == "nearshore")
    ].copy()
    if chosen.segment_id.astype(str).duplicated().any():
        raise ViewerError(f"Optical period {period_id} is not one-to-one by segment_id")
    chosen["segment_id"] = chosen.segment_id.astype(str)
    drop = [
        column
        for column in chosen.columns
        if column not in {"segment_id", "period_id", "zone_type", "configured_months"}
    ]

    def joined(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        base = frame.drop(columns=[column for column in drop if column in frame], errors="ignore")
        result = base.merge(
            chosen[["segment_id", *drop]], on="segment_id", how="left", validate="one_to_one"
        )
        return gpd.GeoDataFrame(result, geometry=frame.geometry.name, crs=frame.crs)

    return replace(
        data,
        analytical_segments=joined(data.analytical_segments),
        display_segments=joined(data.display_segments),
    )


@st.cache_data(show_spinner=False)
def _load_transects_cached(
    path_text: str,
    signature: tuple[str, int, int, str],
) -> gpd.GeoDataFrame:
    del signature
    path = Path(path_text)
    frame = gpd.read_parquet(path)
    required = {"bathymetry_transect_id", "segment_id", "geometry"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ViewerError(f"Bathymetry transects are missing: {', '.join(missing)}")
    return frame


def load_display_transects(
    data: ViewerData,
    segment_ids: set[str] | None = None,
) -> gpd.GeoDataFrame:
    """Lazily load transects only after their layer is enabled."""
    if not data.has_bathymetry or not data.paths.bathymetry_transects.is_file():
        return gpd.GeoDataFrame(
            columns=["bathymetry_transect_id", "segment_id", "geometry"],
            geometry="geometry",
            crs=DISPLAY_CRS,
        )
    path = data.paths.bathymetry_transects
    frame = _load_transects_cached(str(path), _signature(path))
    ambiguous = set(
        data.display_segments.loc[
            data.display_segments.get("orientation_status", "") == "ambiguous", "segment_id"
        ].astype(str)
    )
    display, _ = validate_transect_geometry(
        frame,
        data.analytical_segments,
        ambiguous_segment_ids=ambiguous,
        maximum_length_m=data.maximum_bathymetry_transect_length_m,
    )
    if segment_ids is not None:
        display = display[display.segment_id.astype(str).isin(segment_ids)]
    return display.copy()


def cache_fingerprint(data: ViewerData) -> dict[str, Any]:
    """Expose non-secret cache provenance for the quality page."""
    return {
        "region_id": data.region_id,
        "mode": data.mode,
        "source_crs": data.source_crs,
        "display_crs": DISPLAY_CRS,
        "segment_checksum": data.segment_checksum,
        "geometry_source": str(data.geometry_source),
        "attribute_source": str(data.attribute_source),
        "geometry_checksum": data.geometry_checksum,
        "attribute_checksum": data.attribute_checksum,
        "geometry_validation": data.geometry_validation.as_dict(),
    }
