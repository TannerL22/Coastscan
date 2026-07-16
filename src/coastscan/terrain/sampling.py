"""Batch sample prepared terrain along inland transects."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from shapely import line_interpolate_point


def _value(sample: np.ndarray, nodata: float | None) -> float:
    value = float(sample[0])
    return (
        float("nan")
        if not np.isfinite(value) or (nodata is not None and value == nodata)
        else value
    )


def sample_terrain(
    transects: gpd.GeoDataFrame,
    dem_path: Path,
    slope_path: Path,
    roughness_path: Path,
    sample_spacing_m: float,
    origin_search_max_distance_m: float = 0.0,
) -> pd.DataFrame:
    inland = transects.loc[transects.direction == "inland"].copy()
    rows: list[dict[str, object]] = []
    with (
        rasterio.open(dem_path) as dem,
        rasterio.open(slope_path) as slope,
        rasterio.open(roughness_path) as roughness,
    ):
        for _, transect in inland.iterrows():
            distances = np.arange(0.0, transect.geometry.length + 1e-7, sample_spacing_m)
            if not np.isclose(distances[-1], transect.geometry.length):
                distances = np.append(distances, transect.geometry.length)
            points = [
                line_interpolate_point(transect.geometry, float(distance)) for distance in distances
            ]
            coords = [(point.x, point.y) for point in points]
            elevations = [_value(value, dem.nodata) for value in dem.sample(coords, masked=False)]
            slopes = [_value(value, slope.nodata) for value in slope.sample(coords, masked=False)]
            roughnesses = [
                _value(value, roughness.nodata) for value in roughness.sample(coords, masked=False)
            ]
            origin_candidates = [
                (float(distance), elevation)
                for distance, elevation in zip(distances, elevations, strict=True)
                if distance <= origin_search_max_distance_m + 1e-7 and np.isfinite(elevation)
            ]
            if origin_candidates:
                origin_shift, origin_elevation = origin_candidates[0]
                origin_method = "exact" if np.isclose(origin_shift, 0) else "shifted_inland"
                origin_quality = "good" if origin_method == "exact" else "shifted"
            else:
                origin_shift = float("nan")
                origin_elevation = float("nan")
                origin_method = "unavailable"
                origin_quality = "no_valid_inland_origin"
            for distance, elevation, slope_value, roughness_value in zip(
                distances, elevations, slopes, roughnesses, strict=True
            ):
                rows.append(
                    {
                        "segment_id": transect.segment_id,
                        "transect_id": transect.transect_id,
                        "sample_distance_m": float(distance),
                        "elevation_m": elevation,
                        "slope_deg": slope_value,
                        "roughness": roughness_value,
                        "valid_elevation": bool(np.isfinite(elevation)),
                        "valid_slope": bool(np.isfinite(slope_value)),
                        "terrain_origin_method": origin_method,
                        "terrain_origin_shift_m": origin_shift,
                        "terrain_origin_elevation_m": origin_elevation,
                        "terrain_origin_quality_flag": origin_quality,
                    }
                )
    columns = [
        "segment_id",
        "transect_id",
        "sample_distance_m",
        "elevation_m",
        "slope_deg",
        "roughness",
        "valid_elevation",
        "valid_slope",
        "terrain_origin_method",
        "terrain_origin_shift_m",
        "terrain_origin_elevation_m",
        "terrain_origin_quality_flag",
    ]
    return pd.DataFrame(rows, columns=columns)
