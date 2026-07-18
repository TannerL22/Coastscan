"""Segment-owned seaward analysis zones derived from authoritative orientation."""

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point
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


def generate_optical_zones(
    segments: gpd.GeoDataFrame,
    land_geometry: Any,
    zone_config: Any,
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
            for zone_type in ZONE_RANGES:
                records.append(
                    {
                        "zone_id": f"{segment_id}:{zone_type}",
                        "segment_id": segment_id,
                        "zone_type": zone_type,
                        "zone_status": "ambiguous_orientation",
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
            geometry = unary_union(strips).difference(land_geometry)
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
                    "zone_status": status,
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
    return generate_optical_zones(segments.to_crs(config.analysis_crs), land, settings.zones)
