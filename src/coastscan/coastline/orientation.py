"""Determine landward and seaward sides without silent guessing."""

import math

import geopandas as gpd
from shapely import line_interpolate_point, prepare
from shapely.geometry import MultiPolygon, Point, Polygon


def point_at_bearing(origin: Point, bearing_deg: float, distance_m: float) -> Point:
    radians = math.radians(bearing_deg)
    return Point(
        origin.x + math.sin(radians) * distance_m, origin.y + math.cos(radians) * distance_m
    )


def orient_segments(
    segments: gpd.GeoDataFrame,
    land: Polygon | MultiPolygon,
    test_distance_m: float,
    fallback_distances_m: list[float],
) -> gpd.GeoDataFrame:
    """Classify normals using covers; unresolved cases remain explicitly ambiguous."""
    prepare(land)
    records: list[dict[str, object]] = []
    for _, segment in segments.iterrows():
        row = segment.to_dict()
        midpoint = line_interpolate_point(segment.geometry, segment.geometry.length / 2)
        attempts: list[float] = []
        resolved: tuple[float, float, Point, Point, float] | None = None
        for distance in [test_distance_m, *fallback_distances_m]:
            attempts.append(float(distance))
            left = point_at_bearing(midpoint, segment.left_normal_deg, distance)
            right = point_at_bearing(midpoint, segment.right_normal_deg, distance)
            left_land, right_land = land.covers(left), land.covers(right)
            if left_land != right_land:
                if left_land:
                    resolved = (
                        segment.left_normal_deg,
                        segment.right_normal_deg,
                        left,
                        right,
                        float(distance),
                    )
                else:
                    resolved = (
                        segment.right_normal_deg,
                        segment.left_normal_deg,
                        right,
                        left,
                        float(distance),
                    )
                break
        if resolved:
            land_bearing, sea_bearing, land_point, sea_point, distance = resolved
            status = "resolved" if len(attempts) == 1 else "resolved_fallback"
            warning = None
        else:
            land_bearing = sea_bearing = float("nan")
            distance = float(attempts[-1])
            land_point = point_at_bearing(midpoint, segment.left_normal_deg, distance)
            sea_point = point_at_bearing(midpoint, segment.right_normal_deg, distance)
            status = "ambiguous"
            warning = "Both or neither normal test points classified as land at every distance"
        row.update(
            {
                "orientation_status": status,
                "orientation_test_distance_m": distance,
                "landward_bearing_deg": land_bearing,
                "seaward_bearing_deg": sea_bearing,
                "land_test_point": land_point,
                "sea_test_point": sea_point,
                "orientation_attempts": attempts,
                "orientation_warning": warning,
            }
        )
        records.append(row)
    result = gpd.GeoDataFrame(records, geometry="geometry", crs=segments.crs)
    # Mark auxiliary point columns as geometry extension arrays so GeoParquet can
    # encode their CRS and geometry type rather than treating Shapely objects as Python objects.
    result["land_test_point"] = gpd.GeoSeries(result["land_test_point"], crs=segments.crs)
    result["sea_test_point"] = gpd.GeoSeries(result["sea_test_point"], crs=segments.crs)
    return result
