"""Land-vector loading and minimal validity repair."""

from dataclasses import dataclass
from pathlib import Path

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


def load_land(path: Path, layer: str | None, analysis_crs: str) -> LandLoadResult:
    if not path.is_file():
        raise MissingInputError(
            f"Missing required land polygon: {path}\n"
            "Add the configured vector or update the region YAML."
        )
    try:
        frame = gpd.read_file(path, layer=layer)
    except Exception as exc:
        raise InvalidGeometryError(f"Could not read land polygon {path}: {exc}") from exc
    if frame.crs is None:
        raise InvalidGeometryError(f"Land polygon has no CRS: {path}")
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
    geometry = unary_union(projected.geometry.array)
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise InvalidGeometryError("Union of land features did not produce polygonal geometry")
    return LandLoadResult(
        geometry=geometry,
        original_crs=original_crs,
        feature_count=len(frame),
        originally_valid=originally_valid,
        repair_applied=repair_applied,
        final_area_m2=float(geometry.area),
    )
