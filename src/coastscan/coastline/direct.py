"""Direct authoritative coastline ingestion with source-level audit output."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon

from coastscan.exceptions import InvalidGeometryError, MissingInputError
from coastscan.io.vectors import apply_attribute_filters


@dataclass(frozen=True)
class DirectCoastlineStats:
    source_feature_count: int
    clipped_feature_count: int
    selected_feature_count: int
    source_length_m: float
    clipped_length_m: float
    selected_length_m: float
    suspected_duplicate_count: int
    original_crs: str
    available_fields: tuple[str, ...]


@dataclass(frozen=True)
class DirectCoastlineResult:
    coastline: gpd.GeoDataFrame
    audit: gpd.GeoDataFrame
    stats: DirectCoastlineStats


def load_direct_coastline(
    path: Path,
    *,
    layer: str | None,
    analysis_crs: str,
    aoi: Polygon | MultiPolygon,
    region_id: str,
    source_id: str,
    source_checksum: str,
    processing_version: str,
    feature_filters: list[Any],
    source_id_field: str | None,
    source_class_field: str | None,
    duplicate_tolerance_m: float,
) -> DirectCoastlineResult:
    """Load, filter, clip, explode, and provenance-link a direct line source."""
    if not path.is_file():
        raise MissingInputError(
            f"Missing required direct coastline: {path}\n"
            "Run acquire-region-data or update the configured coastline path."
        )
    try:
        header = gpd.read_file(path, layer=layer, rows=1)
    except Exception as exc:
        raise InvalidGeometryError(f"Could not read direct coastline {path}: {exc}") from exc
    if header.crs is None:
        raise InvalidGeometryError(f"Direct coastline has no CRS: {path}")
    original_crs = str(header.crs)
    source_aoi = gpd.GeoSeries([aoi], crs=analysis_crs).to_crs(header.crs).iloc[0]
    frame = gpd.read_file(path, layer=layer, bbox=source_aoi.bounds)
    if frame.empty:
        raise InvalidGeometryError("Direct coastline has no features intersecting the AOI bounds")
    if not frame.geom_type.isin(["LineString", "MultiLineString"]).all():
        bad_types = sorted(set(frame.geom_type) - {"LineString", "MultiLineString"})
        raise InvalidGeometryError(
            f"Direct coastline requires line geometry; found: {', '.join(bad_types)}"
        )
    available_fields = tuple(str(column) for column in frame.columns if column != "geometry")
    if source_id_field and source_id_field not in frame.columns:
        raise InvalidGeometryError(f"Direct coastline source_id_field is absent: {source_id_field}")
    if source_class_field and source_class_field not in frame.columns:
        raise InvalidGeometryError(
            f"Direct coastline source_class_field is absent: {source_class_field}"
        )
    projected = frame.to_crs(analysis_crs).reset_index(drop=True)
    projected["_original_length_m"] = projected.length.astype(float)
    source_length_m = float(projected._original_length_m.sum())
    clipped = gpd.clip(projected, aoi).loc[lambda value: ~value.geometry.is_empty].copy()
    if clipped.empty:
        raise InvalidGeometryError("Direct coastline does not intersect the configured AOI")
    clipped["_clipped_length_m"] = clipped.length.astype(float)
    clipped_length_m = float(clipped._clipped_length_m.sum())
    selected_index = apply_attribute_filters(clipped, feature_filters).index
    clipped["selected_for_analysis"] = clipped.index.isin(selected_index)
    filter_description = (
        " AND ".join(
            f"{predicate.field} in {predicate.accepted_values}"
            if predicate.accepted_values is not None
            else f"{predicate.field} starts with {predicate.starts_with}"
            for predicate in feature_filters
        )
        or "all line features"
    )
    clipped["selection_reason"] = clipped.selected_for_analysis.map(
        lambda selected: (
            f"selected: {filter_description}"
            if selected
            else f"rejected: does not match {filter_description}"
        )
    )
    clipped["source_feature_id"] = (
        clipped[source_id_field].astype(str)
        if source_id_field
        else clipped.index.map(lambda value: f"source_{value}")
    )
    clipped["source_class"] = (
        clipped[source_class_field].astype(str) if source_class_field else "UNCLASSIFIED"
    )
    selected = clipped.loc[clipped.selected_for_analysis].copy()
    if selected.empty:
        raise InvalidGeometryError("Direct coastline filters selected zero AOI features")
    selected_union = selected.geometry.union_all()
    clipped["suspected_parallel_duplicate"] = False
    rejected = clipped.loc[~clipped.selected_for_analysis]
    if len(rejected) and duplicate_tolerance_m > 0:
        duplicate_flags = rejected.geometry.map(
            lambda geometry: (
                geometry.interpolate(0.5, normalized=True).distance(selected_union)
                <= duplicate_tolerance_m
            )
        )
        clipped.loc[rejected.index, "suspected_parallel_duplicate"] = duplicate_flags
    audit = clipped.rename(
        columns={
            "_original_length_m": "original_length_m",
            "_clipped_length_m": "clipped_length_m",
        }
    )
    audit["source_id"] = source_id
    exploded = selected.explode(index_parts=True).reset_index(drop=True)
    exploded = exploded.loc[exploded.geom_type == "LineString"].copy()
    exploded["coastline_part_id"] = [
        f"{region_id}_direct_{number:05d}" for number in range(len(exploded))
    ]
    exploded["region_id"] = region_id
    exploded["shoreline_type"] = "direct_high_water"
    exploded["source_id"] = source_id
    exploded["source_checksum"] = source_checksum
    exploded["processing_version"] = processing_version
    coastline_columns = [
        "coastline_part_id",
        "region_id",
        "shoreline_type",
        "source_id",
        "source_checksum",
        "processing_version",
        "source_feature_id",
        "source_class",
        "geometry",
    ]
    coastline = gpd.GeoDataFrame(exploded[coastline_columns], geometry="geometry", crs=analysis_crs)
    return DirectCoastlineResult(
        coastline=coastline,
        audit=gpd.GeoDataFrame(audit, geometry="geometry", crs=analysis_crs),
        stats=DirectCoastlineStats(
            source_feature_count=len(frame),
            clipped_feature_count=len(clipped),
            selected_feature_count=int(clipped.selected_for_analysis.sum()),
            source_length_m=source_length_m,
            clipped_length_m=clipped_length_m,
            selected_length_m=float(coastline.length.sum()),
            suspected_duplicate_count=int(clipped.suspected_parallel_duplicate.sum()),
            original_crs=original_crs,
            available_fields=available_fields,
        ),
    )
