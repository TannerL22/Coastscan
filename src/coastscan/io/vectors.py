"""Land-vector loading and minimal validity repair."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from coastscan.exceptions import InvalidGeometryError, MissingInputError


@dataclass(frozen=True)
class LandLoadResult:
    geometry: Polygon | MultiPolygon
    original_crs: str
    feature_count: int
    originally_valid: bool
    repair_applied: bool
    final_area_m2: float
    selected_records: gpd.GeoDataFrame
    source_feature_count: int


def apply_attribute_filters(frame: gpd.GeoDataFrame, filters: list[Any] | None) -> gpd.GeoDataFrame:
    """Apply validated equality or prefix filters with explicit field failures."""
    selected = frame.copy()
    for predicate in filters or []:
        field = str(predicate.field)
        if field not in selected.columns:
            raise InvalidGeometryError(
                f"Configured selection field '{field}' is absent; available fields: "
                f"{', '.join(map(str, selected.columns))}"
            )
        if predicate.accepted_values is not None:
            selected = selected.loc[selected[field].isin(predicate.accepted_values)]
        else:
            selected = selected.loc[
                selected[field].astype(str).str.startswith(predicate.starts_with)
            ]
    return selected.copy()


def load_land(
    path: Path,
    layer: str | None,
    analysis_crs: str,
    *,
    selection_filters: list[Any] | None = None,
    clip_geometry: Polygon | MultiPolygon | None = None,
    clip_buffer_m: float = 0.0,
) -> LandLoadResult:
    if not path.is_file():
        raise MissingInputError(
            f"Missing required land polygon: {path}\n"
            "Add the configured vector or update the region YAML."
        )
    try:
        frame = gpd.read_file(path, layer=layer)
    except Exception as exc:
        raise InvalidGeometryError(f"Could not read land polygon {path}: {exc}") from exc
    source_feature_count = len(frame)
    if frame.crs is None:
        raise InvalidGeometryError(f"Land polygon has no CRS: {path}")
    frame = apply_attribute_filters(frame, selection_filters)
    if frame.empty or frame.geometry.is_empty.any():
        raise InvalidGeometryError(f"Land polygon contains no usable non-empty geometry: {path}")
    if not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise InvalidGeometryError(
            f"Land input must contain only Polygon/MultiPolygon geometry: {path}"
        )
    original_crs = str(frame.crs)
    originally_valid = bool(frame.geometry.is_valid.all())
    repair_applied = False
    if not originally_valid:
        frame.geometry = frame.geometry.map(make_valid)
        repair_applied = True
        if (
            not frame.geometry.is_valid.all()
            or not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all()
        ):
            raise InvalidGeometryError(
                "Minimal make_valid repair did not yield valid polygonal land"
            )
    projected = frame.to_crs(analysis_crs)
    if clip_geometry is not None:
        projected = gpd.clip(projected, clip_geometry.buffer(clip_buffer_m))
        projected = projected.loc[~projected.geometry.is_empty].copy()
        if projected.empty:
            raise InvalidGeometryError("Selected land polygons do not overlap the configured AOI")
    geometry = unary_union(projected.geometry.array)
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise InvalidGeometryError("Union of land features did not produce polygonal geometry")
    return LandLoadResult(
        geometry=geometry,
        original_crs=original_crs,
        feature_count=len(projected),
        originally_valid=originally_valid,
        repair_applied=repair_applied,
        final_area_m2=float(geometry.area),
        selected_records=projected.reset_index(drop=True),
        source_feature_count=source_feature_count,
    )
