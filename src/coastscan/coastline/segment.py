"""Stable distance-along-line coastline segmentation."""

import geopandas as gpd
from shapely import line_interpolate_point
from shapely.geometry import LineString
from shapely.ops import substring


def segment_breaks(length: float, target: float, minimum: float) -> list[tuple[float, float]]:
    """Return intervals, redistributing a short remainder across preceding segments."""
    if length <= target:
        return [(0.0, length)]
    complete = int(length // target)
    remainder = length - complete * target
    if remainder and remainder < minimum:
        width = length / complete
        return [(i * width, (i + 1) * width) for i in range(complete)]
    endpoints = [i * target for i in range(complete + 1)]
    if remainder > 1e-8:
        endpoints.append(length)
    return list(zip(endpoints[:-1], endpoints[1:], strict=True))


def local_bearings(
    line: LineString, distance: float, sample_distance: float = 5.0
) -> dict[str, float]:
    """Calculate a local tangent using points around a distance-along-line location."""
    window = min(sample_distance, line.length / 4)
    before = line_interpolate_point(line, max(0.0, distance - window))
    after = line_interpolate_point(line, min(line.length, distance + window))
    dx, dy = after.x - before.x, after.y - before.y
    import math

    bearing = math.degrees(math.atan2(dx, dy)) % 360
    return {
        "coast_bearing_deg": bearing,
        "reverse_bearing_deg": (bearing + 180) % 360,
        "left_normal_deg": (bearing - 90) % 360,
        "right_normal_deg": (bearing + 90) % 360,
    }


def segment_coastline(
    coastline: gpd.GeoDataFrame,
    *,
    region_id: str,
    coastline_version: str,
    target_length_m: float,
    minimum_length_m: float,
) -> gpd.GeoDataFrame:
    records: list[dict[str, object]] = []
    for _, part in coastline.sort_values("coastline_part_id").iterrows():
        line = part.geometry
        for number, (start, end) in enumerate(
            segment_breaks(line.length, target_length_m, minimum_length_m)
        ):
            geometry = substring(line, start, end)
            if not isinstance(geometry, LineString) or geometry.is_empty:
                continue
            midpoint_distance = geometry.length / 2
            midpoint = line_interpolate_point(geometry, midpoint_distance)
            bearings = local_bearings(geometry, midpoint_distance)
            records.append(
                {
                    "segment_id": (
                        f"{region_id}_{coastline_version}_{part.coastline_part_id}_{number:05d}"
                    ),
                    "region_id": region_id,
                    "coastline_part_id": part.coastline_part_id,
                    "segment_number": number,
                    "segment_length_m": float(geometry.length),
                    "start_distance_m": float(start),
                    "end_distance_m": float(end),
                    "coastline_version": coastline_version,
                    "midpoint_x": midpoint.x,
                    "midpoint_y": midpoint.y,
                    **bearings,
                    "geometry": geometry,
                }
            )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=coastline.crs)
