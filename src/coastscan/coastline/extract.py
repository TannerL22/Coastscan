"""Extract exterior coasts and separately classified interior shorelines."""

from collections.abc import Iterable

import geopandas as gpd
from shapely.geometry import LineString, MultiPolygon, Polygon


def _polygons(land: Polygon | MultiPolygon) -> Iterable[Polygon]:
    return [land] if isinstance(land, Polygon) else land.geoms


def extract_coastline(
    land: Polygon | MultiPolygon,
    *,
    region_id: str,
    source_id: str,
    source_checksum: str,
    processing_version: str,
    crs: str,
    include_interior: bool = False,
) -> gpd.GeoDataFrame:
    """Extract individual exterior rings, retaining optional lake shorelines."""
    records: list[dict[str, object]] = []
    part = 0
    for polygon in _polygons(land):
        rings: list[tuple[str, LineString]] = [("exterior", LineString(polygon.exterior.coords))]
        rings.extend(("interior", LineString(ring.coords)) for ring in polygon.interiors)
        for shoreline_type, line in rings:
            if shoreline_type == "interior" and not include_interior:
                continue
            if line.is_empty or line.length == 0:
                continue
            records.append(
                {
                    "coastline_part_id": f"{region_id}_part_{part:04d}",
                    "region_id": region_id,
                    "shoreline_type": shoreline_type,
                    "source_id": source_id,
                    "source_checksum": source_checksum,
                    "processing_version": processing_version,
                    "geometry": line,
                }
            )
            part += 1
    return gpd.GeoDataFrame(records, geometry="geometry", crs=crs)
