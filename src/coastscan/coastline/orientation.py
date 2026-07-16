"""Determine landward and seaward sides without silent guessing."""

import math

import geopandas as gpd
from shapely import line_interpolate_point, prepare
from shapely.geometry import MultiPolygon, Point, Polygon

from coastscan.coastline.segment import local_bearings


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
    vote_offsets_m: list[float] | None = None,
    source_mismatch_tolerance_m: float = 5.0,
) -> gpd.GeoDataFrame:
    """Resolve side by deterministic local votes; never guess tied or absent evidence."""
    prepare(land)
    offsets = vote_offsets_m or [0.0]
    records: list[dict[str, object]] = []
    for _, segment in segments.iterrows():
        row = segment.to_dict()
        midpoint_distance = segment.geometry.length / 2
        midpoint = line_interpolate_point(segment.geometry, midpoint_distance)
        attempts: list[float] = []
        resolved_side: str | None = None
        resolved_distance = float(test_distance_m)
        resolved_left_votes = 0
        resolved_right_votes = 0
        for distance in [test_distance_m, *fallback_distances_m]:
            attempts.append(float(distance))
            left_votes = 0
            right_votes = 0
            for offset in offsets:
                origin_distance = min(
                    segment.geometry.length,
                    max(0.0, midpoint_distance + float(offset)),
                )
                origin = line_interpolate_point(segment.geometry, origin_distance)
                bearings = local_bearings(segment.geometry, origin_distance)
                left = point_at_bearing(origin, bearings["left_normal_deg"], distance)
                right = point_at_bearing(origin, bearings["right_normal_deg"], distance)
                left_land, right_land = land.covers(left), land.covers(right)
                if left_land and not right_land:
                    left_votes += 1
                elif right_land and not left_land:
                    right_votes += 1
            decisive = left_votes + right_votes
            minimum_votes = 1 if len(offsets) == 1 else math.ceil(len(offsets) * 2 / 3)
            if (
                decisive
                and left_votes != right_votes
                and max(left_votes, right_votes) >= minimum_votes
            ):
                resolved_side = "left" if left_votes > right_votes else "right"
                resolved_distance = float(distance)
                resolved_left_votes = left_votes
                resolved_right_votes = right_votes
                break
        if resolved_side:
            land_bearing = (
                segment.left_normal_deg if resolved_side == "left" else segment.right_normal_deg
            )
            sea_bearing = (land_bearing + 180) % 360
            land_point = point_at_bearing(midpoint, land_bearing, resolved_distance)
            sea_point = point_at_bearing(midpoint, sea_bearing, resolved_distance)
            status = "resolved" if len(attempts) == 1 else "resolved_fallback"
            voting = len(offsets) > 1
            orientation_method = ("multi_point_vote" if voting else "single_pair") + (
                "_fallback" if len(attempts) > 1 else ""
            )
            warning = None
            landward_votes = max(resolved_left_votes, resolved_right_votes)
            seaward_votes = min(resolved_left_votes, resolved_right_votes)
        else:
            land_bearing = sea_bearing = float("nan")
            resolved_distance = float(attempts[-1])
            land_point = point_at_bearing(midpoint, segment.left_normal_deg, resolved_distance)
            sea_point = point_at_bearing(midpoint, segment.right_normal_deg, resolved_distance)
            status = "ambiguous"
            orientation_method = "unresolved"
            warning = "Normal-point votes were tied or absent at every configured distance"
            landward_votes = 0
            seaward_votes = 0
        boundary_distance = float(segment.geometry.distance(land.boundary))
        mismatch = boundary_distance > source_mismatch_tolerance_m
        row.update(
            {
                "orientation_status": status,
                "orientation_test_distance_m": resolved_distance,
                "landward_bearing_deg": land_bearing,
                "seaward_bearing_deg": sea_bearing,
                "land_test_point": land_point,
                "sea_test_point": sea_point,
                "orientation_attempts": attempts,
                "orientation_warning": warning,
                "orientation_method": orientation_method,
                "orientation_vote_count_landward": landward_votes,
                "orientation_vote_count_seaward": seaward_votes,
                "coast_to_landmask_boundary_distance_m": boundary_distance,
                "orientation_source_mismatch_flag": mismatch,
            }
        )
        records.append(row)
    result = gpd.GeoDataFrame(records, geometry="geometry", crs=segments.crs)
    # Mark auxiliary point columns as geometry extension arrays so GeoParquet can
    # encode their CRS and geometry type rather than treating Shapely objects as Python objects.
    result["land_test_point"] = gpd.GeoSeries(result["land_test_point"], crs=segments.crs)
    result["sea_test_point"] = gpd.GeoSeries(result["sea_test_point"], crs=segments.crs)
    return result
