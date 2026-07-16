"""Generate locally normal inland and offshore transects."""

import geopandas as gpd
import numpy as np
from shapely import line_interpolate_point
from shapely.geometry import LineString

from coastscan.coastline.orientation import point_at_bearing
from coastscan.coastline.segment import local_bearings


def _angle_distance(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


def generate_transects(
    segments: gpd.GeoDataFrame,
    spacing_m: float,
    inland_length_m: float,
    offshore_length_m: float,
) -> gpd.GeoDataFrame:
    records: list[dict[str, object]] = []
    for _, segment in segments.iterrows():
        if segment.orientation_status not in {"resolved", "resolved_fallback"}:
            continue
        distances = np.arange(0.0, segment.geometry.length + 1e-7, spacing_m)
        if not np.isclose(distances[-1], segment.geometry.length):
            distances = np.append(distances, segment.geometry.length)
        midpoint_left_is_land = (
            _angle_distance(segment.landward_bearing_deg, segment.left_normal_deg) < 90
        )
        for number, distance in enumerate(distances):
            origin = line_interpolate_point(segment.geometry, float(distance))
            bearings = local_bearings(segment.geometry, float(distance))
            landward = (
                bearings["left_normal_deg"]
                if midpoint_left_is_land
                else bearings["right_normal_deg"]
            )
            seaward = (landward + 180) % 360
            for direction, bearing, length in (
                ("inland", landward, inland_length_m),
                ("offshore", seaward, offshore_length_m),
            ):
                endpoint = point_at_bearing(origin, bearing, length)
                records.append(
                    {
                        "transect_id": f"{segment.segment_id}_t{number:03d}_{direction}",
                        "segment_id": segment.segment_id,
                        "transect_number": number,
                        "origin_distance_m": float(distance),
                        "direction": direction,
                        "bearing_deg": float(bearing),
                        "length_m": float(length),
                        "orientation_status": segment.orientation_status,
                        "geometry": LineString([origin, endpoint]),
                    }
                )
    columns = [
        "transect_id",
        "segment_id",
        "transect_number",
        "origin_distance_m",
        "direction",
        "bearing_deg",
        "length_m",
        "orientation_status",
        "geometry",
    ]
    return gpd.GeoDataFrame(records, columns=columns, geometry="geometry", crs=segments.crs)
