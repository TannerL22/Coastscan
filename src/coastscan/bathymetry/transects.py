"""Deterministic long offshore transects for Phase 2."""

import math

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString


def _bearing(a: object, b: object) -> float:
    return float((math.degrees(math.atan2(b.x - a.x, b.y - a.y)) + 360) % 360)  # type: ignore[attr-defined]


def _angular_distance(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def generate_bathymetry_transects(
    segments: gpd.GeoDataFrame,
    *,
    spacing_m: float,
    maximum_distance_m: float,
) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for segment in segments.sort_values("segment_id").itertuples():
        if segment.orientation_status == "ambiguous" or not np.isfinite(
            segment.seaward_bearing_deg
        ):
            continue
        geometry = segment.geometry
        origins = list(np.arange(0.0, geometry.length, spacing_m))
        if not origins or geometry.length - origins[-1] > spacing_m * 0.5:
            origins.append(float(geometry.length))
        for number, distance in enumerate(origins):
            origin = geometry.interpolate(distance)
            before = geometry.interpolate(max(0.0, distance - 2.0))
            after = geometry.interpolate(min(geometry.length, distance + 2.0))
            tangent = _bearing(before, after)
            normals = ((tangent - 90) % 360, (tangent + 90) % 360)
            bearing = min(
                normals, key=lambda value: _angular_distance(value, segment.seaward_bearing_deg)
            )
            radians = math.radians(bearing)
            end = type(origin)(
                origin.x + maximum_distance_m * math.sin(radians),
                origin.y + maximum_distance_m * math.cos(radians),
            )
            transect_id = f"{segment.segment_id}_b{number:03d}"
            rows.append(
                {
                    "bathymetry_transect_id": transect_id,
                    "segment_id": segment.segment_id,
                    "transect_number": number,
                    "origin_distance_m": float(distance),
                    "bearing_deg": float(bearing),
                    "maximum_distance_m": float(maximum_distance_m),
                    "orientation_status": segment.orientation_status,
                    "orientation_method": segment.orientation_method,
                    "source_mismatch_flag": bool(segment.orientation_source_mismatch_flag),
                    "geometry": LineString([origin, end]),
                }
            )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=segments.crs)
