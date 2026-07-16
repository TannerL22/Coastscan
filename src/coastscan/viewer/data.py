"""Immutable discovery, validation and cached display reprojection."""

import json
import os
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import streamlit as st

from coastscan.catalog.manifests import sha256_file
from coastscan.config import PROJECT_ROOT, load_region_config
from coastscan.exceptions import ConfigurationError, ViewerError
from coastscan.viewer.models import ViewerData, ViewerPaths

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
        preferred_segments=processed / "segment_features_phase2.parquet",
        phase1_segments=processed / "segment_features.parquet",
        coast_segments=processed / "coast_segments.parquet",
        bathymetry_features=processed / "bathymetry_features.parquet",
        bathymetry_transects=processed / "bathymetry_transects.parquet",
        manifest_directory=project_root / "outputs" / "manifests" / region_id,
    )


def missing_outputs_message(region_id: str) -> str:
    return f"""No processed CoastScan outputs were found for:
{region_id}

Run:

uv run coastscan acquire-region-data --region {region_id}
uv run coastscan build-region --region {region_id} --write-samples
uv run coastscan build-bathymetry --region {region_id} --write-samples"""


def _validate_segments(frame: gpd.GeoDataFrame, path: Path) -> None:
    if frame.crs is None:
        raise ViewerError(f"Processed segment file has no CRS metadata: {path}")
    if "segment_id" not in frame:
        raise ViewerError(f"Processed segment file has no segment_id column: {path}")
    duplicates = frame.loc[frame.segment_id.duplicated(), "segment_id"].astype(str).unique()
    if len(duplicates):
        raise ViewerError(
            f"Processed segment file contains duplicate segment IDs: {', '.join(duplicates[:5])}"
        )
    if frame.empty:
        raise ViewerError(f"Processed segment file contains no segments: {path}")
    invalid = frame.geometry.isna() | frame.geometry.is_empty
    if bool(invalid.any()):
        raise ViewerError(f"Processed segment file contains missing or empty geometry: {path}")
    allowed = {"LineString", "MultiLineString"}
    unsupported = sorted(set(frame.geometry.geom_type) - allowed)
    if unsupported:
        raise ViewerError(
            f"Viewer requires line segment geometry; found {', '.join(unsupported)} in {path}"
        )


def _latest_manifests(directory: Path) -> dict[str, dict[str, object]]:
    if not directory.is_dir():
        return {}
    result: dict[str, dict[str, object]] = {}
    phase1 = sorted(path for path in directory.glob("*.json") if "_bathymetry" not in path.name)
    phase2 = sorted(directory.glob("*_bathymetry.json"))
    for stage, candidates in (("phase1", phase1), ("phase2", phase2)):
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
    path_text: str,
    signature: tuple[str, int, int, str],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, str]:
    del signature
    path = Path(path_text)
    analytical = gpd.read_parquet(path)
    _validate_segments(analytical, path)
    source_crs = analytical.crs.to_string()
    display = analytical.to_crs(DISPLAY_CRS)
    return analytical, display, source_crs


def load_viewer_data(region_id: str, root: Path | None = None) -> ViewerData:
    """Load the preferred joined output or fall back explicitly to terrain-only mode."""
    paths = discover_viewer_paths(region_id, root)
    if paths.preferred_segments.is_file():
        selected = paths.preferred_segments
        mode: Literal["phase2", "terrain_only"] = "phase2"
    elif paths.phase1_segments.is_file():
        selected = paths.phase1_segments
        mode = "terrain_only"
    else:
        raise ViewerError(missing_outputs_message(region_id))
    signature = _signature(selected)
    analytical, display, source_crs = _load_segments_cached(str(selected), signature)
    coastline_source_id: str | None = None
    try:
        config, _ = load_region_config(region_id, (root or viewer_project_root()).resolve())
        coastline_source_id = (
            config.inputs.coastline.source_id
            if config.inputs.coastline is not None
            else config.inputs.land_polygon.source_id
            if config.inputs.land_polygon is not None
            else None
        )
    except ConfigurationError:
        coastline_source_id = None
    return ViewerData(
        region_id=region_id,
        mode=mode,
        analytical_segments=analytical,
        display_segments=display,
        paths=paths,
        source_crs=source_crs,
        segment_checksum=signature[3],
        coastline_source_id=coastline_source_id,
        manifests=_latest_manifests(paths.manifest_directory),
    )


@st.cache_data(show_spinner=False)
def _load_transects_cached(
    path_text: str,
    signature: tuple[str, int, int, str],
) -> gpd.GeoDataFrame:
    del signature
    path = Path(path_text)
    frame = gpd.read_parquet(path)
    if frame.crs is None:
        raise ViewerError(f"Bathymetry transect file has no CRS metadata: {path}")
    required = {"bathymetry_transect_id", "segment_id", "geometry"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ViewerError(f"Bathymetry transects are missing: {', '.join(missing)}")
    return frame.to_crs(DISPLAY_CRS)


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
    contaminated = ambiguous & set(frame.segment_id.astype(str))
    if contaminated:
        raise ViewerError(
            "Bathymetry transects unexpectedly reference ambiguous segments: "
            + ", ".join(sorted(contaminated)[:5])
        )
    if segment_ids is not None:
        frame = frame[frame.segment_id.astype(str).isin(segment_ids)]
    return frame.copy()


def cache_fingerprint(data: ViewerData) -> dict[str, Any]:
    """Expose non-secret cache provenance for the quality page."""
    return {
        "region_id": data.region_id,
        "mode": data.mode,
        "source_crs": data.source_crs,
        "display_crs": DISPLAY_CRS,
        "segment_checksum": data.segment_checksum,
    }
