"""Segment-owned seaward analysis zones derived from authoritative orientation."""

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from coastscan.config import data_path
from coastscan.io.vectors import load_land

ZONE_RANGES = ("nearshore", "coastal", "context")


def _points(line: LineString | MultiLineString, spacing: float) -> list[Point]:
    parts = list(line.geoms) if isinstance(line, MultiLineString) else [line]
    result: list[Point] = []
    for part in parts:
        count = max(1, math.ceil(part.length / spacing))
        result.extend(
            part.interpolate(min(part.length, (index + 0.5) * part.length / count))
            for index in range(count)
        )
    return result


def _offset(point: Point, bearing_degrees: float, distance: float) -> Point:
    angle = math.radians(bearing_degrees)
    return Point(point.x + math.sin(angle) * distance, point.y + math.cos(angle) * distance)


def _clean_polygonal(geometry: Any, minimum_area: float) -> tuple[Any, list[str]]:
    warnings: list[str] = []
    if not geometry.is_valid:
        geometry = make_valid(geometry)
        warnings.append("geometry_repaired")
    raw_parts = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
    parts: list[Polygon] = []
    removed = 0
    for part in raw_parts:
        candidates = list(part.geoms) if isinstance(part, MultiPolygon) else [part]
        for candidate in candidates:
            if isinstance(candidate, Polygon) and candidate.area >= minimum_area:
                parts.append(candidate)
            elif getattr(candidate, "area", 0.0) > 0:
                removed += 1
    if removed:
        warnings.append(f"tiny_slivers_removed:{removed}")
    if len(parts) > 1:
        warnings.append(f"disconnected_valid_parts:{len(parts)}")
    return unary_union(parts), warnings


def generate_optical_zones(
    segments: gpd.GeoDataFrame,
    land_geometry: Any,
    zone_config: Any,
    *,
    land_exclusion_m: float = 0.0,
) -> gpd.GeoDataFrame:
    if segments.crs is None:
        raise ValueError("Optical zone segments require a projected CRS")
    ranges = {
        "nearshore": (zone_config.nearshore_inner_m, zone_config.nearshore_outer_m),
        "coastal": (zone_config.coastal_inner_m, zone_config.coastal_outer_m),
        "context": (zone_config.context_inner_m, zone_config.context_outer_m),
    }
    records: list[dict[str, object]] = []
    spacing = float(zone_config.along_coast_origin_spacing_m)
    for row in segments.sort_values("segment_id").itertuples():
        segment_id = str(row.segment_id)
        orientation = str(getattr(row, "orientation_status", "ambiguous"))
        bearing = getattr(row, "seaward_bearing_deg", None)
        if orientation == "ambiguous" or bearing is None or not math.isfinite(float(bearing)):
            for zone_type, (inner, outer) in ranges.items():
                records.append(
                    {
                        "zone_id": f"{segment_id}:{zone_type}",
                        "segment_id": segment_id,
                        "zone_type": zone_type,
                        "zone_class": zone_type,
                        "inner_distance_m": float(inner),
                        "outer_distance_m": float(outer),
                        "zone_area_m2": 0.0,
                        "orientation_status": orientation,
                        "zone_status": "ambiguous_orientation",
                        "zone_geometry_status": "ambiguous_orientation",
                        "land_exclusion_m": land_exclusion_m,
                        "geometry_warnings": "orientation_unresolved",
                        "geometry": None,
                    }
                )
            continue
        origins = _points(row.geometry, spacing)
        for zone_type, (inner, outer) in ranges.items():
            strips = [
                LineString(
                    [_offset(origin, float(bearing), inner), _offset(origin, float(bearing), outer)]
                ).buffer(spacing / 2, cap_style="flat")
                for origin in origins
            ]
            geometry, warnings = _clean_polygonal(
                unary_union(strips).difference(land_geometry),
                minimum_area=max(1.0, spacing * spacing * 0.02),
            )
            status = (
                "valid"
                if not geometry.is_empty and geometry.area > 0
                else "empty_after_land_exclusion"
            )
            records.append(
                {
                    "zone_id": f"{segment_id}:{zone_type}",
                    "segment_id": segment_id,
                    "zone_type": zone_type,
                    "zone_class": zone_type,
                    "inner_distance_m": float(inner),
                    "outer_distance_m": float(outer),
                    "zone_area_m2": float(geometry.area) if status == "valid" else 0.0,
                    "orientation_status": orientation,
                    "zone_status": status,
                    "zone_geometry_status": status,
                    "land_exclusion_m": land_exclusion_m,
                    "geometry_warnings": ";".join(warnings),
                    "geometry": geometry if status == "valid" else None,
                }
            )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=segments.crs)


def zones_for_region(config: Any, segments: gpd.GeoDataFrame, root: Path) -> gpd.GeoDataFrame:
    source = config.inputs.land_polygon
    settings = config.optical
    if source is None or settings is None:
        raise ValueError("Optical zones require land and optical configuration")
    land = load_land(
        data_path(source.path, root),
        source.layer,
        config.analysis_crs,
        selection_filters=source.selection_filters,
    ).geometry.buffer(settings.masks.minimum_land_exclusion_m)
    return generate_optical_zones(
        segments.to_crs(config.analysis_crs),
        land,
        settings.zones,
        land_exclusion_m=settings.masks.minimum_land_exclusion_m,
    )
