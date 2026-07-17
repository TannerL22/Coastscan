"""Hard spatial contracts for viewer coastline and transect geometry."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry

from coastscan.exceptions import ViewerError

DISPLAY_CRS = "EPSG:4326"
Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class GeometryValidationResult:
    """Serializable evidence produced by a successful geometry validation."""

    feature_count: int
    geometry_types: dict[str, int]
    native_bounds: Bounds
    wgs84_bounds: Bounds
    length_min_m: float
    length_p50_m: float
    length_p90_m: float
    length_max_m: float
    maximum_coordinate_jump_m: float
    invalid_geometry_count: int
    invalid_coordinate_count: int
    out_of_aoi_count: int
    aoi_bounds_wgs84: Bounds | None = None
    centroid_distance_to_aoi_m: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def geometry_columns(frame: pd.DataFrame) -> list[str]:
    """Return every GeoPandas geometry-typed column, active or otherwise."""
    return [str(column) for column in frame.columns if str(frame[column].dtype) == "geometry"]


def _line_parts(geometry: BaseGeometry) -> Iterator[LineString]:
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms


def line_parts(geometry: BaseGeometry) -> tuple[LineString, ...]:
    """Expose independent line components without ever concatenating them."""
    return tuple(_line_parts(geometry))


def _coordinates(geometries: Iterable[BaseGeometry]) -> Iterator[tuple[float, float]]:
    for geometry in geometries:
        for part in _line_parts(geometry):
            for coordinate in part.coords:
                yield float(coordinate[0]), float(coordinate[1])


def _maximum_projected_jump(frame: gpd.GeoDataFrame) -> float:
    maximum = 0.0
    for geometry in frame.geometry:
        for part in _line_parts(geometry):
            coordinates = np.asarray(part.coords, dtype="float64")[:, :2]
            if len(coordinates) > 1:
                jumps = np.linalg.norm(np.diff(coordinates, axis=0), axis=1)
                maximum = max(maximum, float(jumps.max()))
    return maximum


def _bounds(values: np.ndarray) -> Bounds:
    return tuple(float(value) for value in values)  # type: ignore[return-value]


def _validate_frame_contract(
    frame: gpd.GeoDataFrame,
    *,
    label: str,
    id_column: str,
    allowed_types: set[str],
) -> None:
    if not isinstance(frame, gpd.GeoDataFrame):
        raise ViewerError(f"{label} is not a GeoDataFrame with an active geometry column.")
    active = getattr(frame, "_geometry_column_name", None)
    if not active or active not in frame.columns:
        raise ViewerError(f"{label} has no active geometry column.")
    if frame.crs is None:
        raise ViewerError(f"{label} has no CRS metadata; rendering was stopped.")
    if id_column not in frame.columns:
        raise ViewerError(f"{label} has no {id_column} column.")
    if frame.empty:
        raise ViewerError(f"{label} contains no features.")
    duplicate_ids = frame.loc[frame[id_column].astype(str).duplicated(), id_column].astype(str)
    if not duplicate_ids.empty:
        examples = ", ".join(duplicate_ids.unique()[:5])
        raise ViewerError(f"{label} contains duplicate {id_column} values: {examples}")
    missing = frame.geometry.isna() | frame.geometry.is_empty
    if bool(missing.any()):
        raise ViewerError(f"{label} contains {int(missing.sum())} missing or empty geometries.")
    types = set(frame.geometry.geom_type.astype(str))
    unsupported = sorted(types - allowed_types)
    if unsupported:
        raise ViewerError(
            f"{label} requires {', '.join(sorted(allowed_types))} geometry; found "
            f"{', '.join(unsupported)}."
        )
    invalid = ~frame.geometry.is_valid
    if bool(invalid.any()):
        raise ViewerError(f"{label} contains {int(invalid.sum())} invalid line geometries.")
    if not frame.crs.is_projected:
        raise ViewerError(
            f"{label} must retain its projected analytical CRS before display conversion; "
            f"found {frame.crs.to_string()}."
        )


def _validate_display_coordinates(
    display: gpd.GeoDataFrame,
    *,
    label: str,
) -> int:
    coordinates = np.asarray(list(_coordinates(display.geometry)), dtype="float64")
    if coordinates.size == 0:
        raise ViewerError(f"{label} contains no line coordinates.")
    finite = np.isfinite(coordinates)
    invalid_count = int((~finite).sum())
    if invalid_count:
        raise ViewerError(f"{label} contains {invalid_count} NaN or infinite coordinate values.")
    longitude_invalid = (coordinates[:, 0] < -180) | (coordinates[:, 0] > 180)
    if bool(longitude_invalid.any()):
        value = float(coordinates[longitude_invalid, 0][0])
        raise ViewerError(
            f"{label} contains longitude {value:.6f} outside the valid -180 to 180 range."
        )
    latitude_invalid = (coordinates[:, 1] < -90) | (coordinates[:, 1] > 90)
    if bool(latitude_invalid.any()):
        value = float(coordinates[latitude_invalid, 1][0])
        raise ViewerError(
            f"{label} contains latitude {value:.6f} outside the valid -90 to 90 range."
        )
    return invalid_count


def validate_line_geometry(
    frame: gpd.GeoDataFrame,
    *,
    label: str,
    id_column: str = "segment_id",
    allowed_types: set[str] | None = None,
    aoi: gpd.GeoDataFrame | None = None,
    aoi_buffer_m: float = 1_500.0,
    maximum_coordinate_jump_m: float = 10_000.0,
    maximum_geometry_length_m: float = 10_000.0,
) -> tuple[gpd.GeoDataFrame, GeometryValidationResult]:
    """Validate projected line geometry and return a separate WGS84 display copy."""
    accepted = allowed_types or {"LineString", "MultiLineString"}
    _validate_frame_contract(
        frame,
        label=label,
        id_column=id_column,
        allowed_types=accepted,
    )
    analytical = frame.copy()
    lengths = analytical.geometry.length.astype("float64")
    maximum_length = float(lengths.max())
    if maximum_length > maximum_geometry_length_m:
        raise ViewerError(
            f"{label} contains a {maximum_length:.1f} m line, exceeding the configured "
            f"{maximum_geometry_length_m:.1f} m regional sanity limit."
        )
    maximum_jump = _maximum_projected_jump(analytical)
    if maximum_jump > maximum_coordinate_jump_m:
        raise ViewerError(
            f"{label} contains a {maximum_jump:.1f} m coordinate jump, exceeding the configured "
            f"{maximum_coordinate_jump_m:.1f} m regional sanity limit."
        )
    display = analytical.to_crs(DISPLAY_CRS)
    invalid_coordinate_count = _validate_display_coordinates(display, label=label)

    out_of_aoi_count = 0
    aoi_bounds: Bounds | None = None
    centroid_distance: float | None = None
    if aoi is not None:
        if not isinstance(aoi, gpd.GeoDataFrame) or aoi.crs is None or aoi.empty:
            raise ViewerError(f"{label} AOI is empty or has no CRS metadata.")
        projected_aoi = aoi.to_crs(analytical.crs)
        aoi_union = projected_aoi.geometry.union_all()
        if aoi_union.is_empty:
            raise ViewerError(f"{label} AOI contains no usable geometry.")
        buffered = aoi_union.buffer(aoi_buffer_m)
        out_of_aoi = ~analytical.geometry.intersects(buffered)
        out_of_aoi_count = int(out_of_aoi.sum())
        if out_of_aoi_count:
            examples = ", ".join(analytical.loc[out_of_aoi, id_column].astype(str).head(5).tolist())
            raise ViewerError(
                f"{label} has {out_of_aoi_count} features outside the AOI plus "
                f"{aoi_buffer_m:.0f} m tolerance: {examples}"
            )
        left, bottom, right, top = analytical.total_bounds
        aoi_left, aoi_bottom, aoi_right, aoi_top = buffered.bounds
        if left < aoi_left or bottom < aoi_bottom or right > aoi_right or top > aoi_top:
            raise ViewerError(
                f"{label} bounds {_bounds(analytical.total_bounds)} are materially larger than "
                f"the AOI plus {aoi_buffer_m:.0f} m tolerance."
            )
        centroid_distance = float(aoi_union.distance(analytical.geometry.union_all().centroid))
        if centroid_distance > aoi_buffer_m:
            raise ViewerError(
                f"{label} centroid is {centroid_distance:.1f} m from the AOI, beyond the "
                f"{aoi_buffer_m:.1f} m tolerance."
            )
        aoi_bounds = _bounds(projected_aoi.to_crs(DISPLAY_CRS).total_bounds)

    result = GeometryValidationResult(
        feature_count=len(analytical),
        geometry_types={
            str(name): int(count)
            for name, count in analytical.geometry.geom_type.value_counts().items()
        },
        native_bounds=_bounds(analytical.total_bounds),
        wgs84_bounds=_bounds(display.total_bounds),
        length_min_m=float(lengths.min()),
        length_p50_m=float(lengths.quantile(0.5)),
        length_p90_m=float(lengths.quantile(0.9)),
        length_max_m=maximum_length,
        maximum_coordinate_jump_m=maximum_jump,
        invalid_geometry_count=0,
        invalid_coordinate_count=invalid_coordinate_count,
        out_of_aoi_count=out_of_aoi_count,
        aoi_bounds_wgs84=aoi_bounds,
        centroid_distance_to_aoi_m=centroid_distance,
    )
    return display, result


def validate_display_line_geometry(
    frame: gpd.GeoDataFrame,
    *,
    label: str,
    maximum_jump_km: float = 50.0,
) -> None:
    """Protect layer constructors from malformed or non-WGS84 display frames."""
    if not isinstance(frame, gpd.GeoDataFrame) or frame.crs is None:
        raise ViewerError(f"{label} has no display CRS metadata.")
    if frame.crs.to_epsg() != 4326:
        raise ViewerError(f"{label} requires EPSG:4326 display geometry.")
    if frame.empty:
        return
    active = getattr(frame, "_geometry_column_name", None)
    if not active or active not in frame.columns:
        raise ViewerError(f"{label} has no active geometry column.")
    missing = frame.geometry.isna() | frame.geometry.is_empty
    if bool(missing.any()):
        raise ViewerError(f"{label} contains missing or empty display geometry.")
    unsupported = sorted(
        set(frame.geometry.geom_type.astype(str)) - {"LineString", "MultiLineString"}
    )
    if unsupported:
        raise ViewerError(
            f"{label} requires line display geometry; found {', '.join(unsupported)}."
        )
    if bool((~frame.geometry.is_valid).any()):
        raise ViewerError(f"{label} contains invalid display line geometry.")
    _validate_display_coordinates(frame, label=label)
    earth_radius_km = 6_371.0088
    maximum = 0.0
    for geometry in frame.geometry:
        for part in _line_parts(geometry):
            coordinates = list(part.coords)
            for first, second in zip(coordinates, coordinates[1:], strict=False):
                lon1, lat1 = math.radians(first[0]), math.radians(first[1])
                lon2, lat2 = math.radians(second[0]), math.radians(second[1])
                dlon, dlat = lon2 - lon1, lat2 - lat1
                value = (
                    math.sin(dlat / 2) ** 2
                    + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
                )
                maximum = max(maximum, 2 * earth_radius_km * math.asin(min(1.0, math.sqrt(value))))
    if maximum > maximum_jump_km:
        raise ViewerError(
            f"{label} contains a {maximum:.1f} km WGS84 coordinate jump, exceeding the "
            f"{maximum_jump_km:.1f} km display sanity limit."
        )


def validate_transect_geometry(
    frame: gpd.GeoDataFrame,
    parent_segments: gpd.GeoDataFrame,
    *,
    ambiguous_segment_ids: set[str],
    maximum_length_m: float,
    origin_tolerance_m: float = 50.0,
) -> tuple[gpd.GeoDataFrame, GeometryValidationResult]:
    """Validate independent bathymetry paths against authoritative parent segments."""
    display, result = validate_line_geometry(
        frame,
        label="Bathymetry transects",
        id_column="bathymetry_transect_id",
        allowed_types={"LineString"},
        maximum_coordinate_jump_m=maximum_length_m * 1.05,
        maximum_geometry_length_m=maximum_length_m * 1.05,
    )
    if "segment_id" not in frame.columns:
        raise ViewerError("Bathymetry transects have no parent segment_id column.")
    parent_ids = set(parent_segments.segment_id.astype(str))
    referenced = set(frame.segment_id.astype(str))
    unknown = sorted(referenced - parent_ids)
    if unknown:
        raise ViewerError(
            "Bathymetry transects reference unknown parent segments: " + ", ".join(unknown[:5])
        )
    contaminated = referenced & ambiguous_segment_ids
    if contaminated:
        raise ViewerError(
            "Bathymetry transects unexpectedly reference ambiguous segments: "
            + ", ".join(sorted(contaminated)[:5])
        )
    projected_parents = parent_segments.to_crs(frame.crs).set_index(
        parent_segments.segment_id.astype(str)
    )
    origin_distances = []
    for _, row in frame.iterrows():
        origin = Point(row.geometry.coords[0])
        parent = projected_parents.loc[str(row.segment_id)].geometry
        origin_distances.append(float(origin.distance(parent)))
    maximum_origin_distance = max(origin_distances, default=0.0)
    if maximum_origin_distance > origin_tolerance_m:
        raise ViewerError(
            f"Bathymetry transect origin is {maximum_origin_distance:.1f} m from its parent "
            f"coastline segment, beyond the {origin_tolerance_m:.1f} m tolerance."
        )
    return display, result


def load_aoi(path: Path, layer: str | None = None) -> gpd.GeoDataFrame:
    """Read configured AOI geometry with an actionable viewer-specific error."""
    if not path.is_file():
        raise ViewerError(f"Configured viewer AOI does not exist: {path}")
    try:
        return gpd.read_file(path, layer=layer)
    except Exception as exc:
        raise ViewerError(f"Could not read configured viewer AOI {path}: {exc}") from exc
