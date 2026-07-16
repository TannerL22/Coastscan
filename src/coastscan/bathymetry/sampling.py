"""Resolution-aware continuous and categorical bathymetry sampling."""

import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import Affine

from coastscan.bathymetry.prepare import PreparedBathymetry
from coastscan.models.region import BathymetryConfig


@dataclass(frozen=True)
class RasterLayer:
    array: np.ndarray
    transform: Affine


def _load_layers(paths: dict[str, Path]) -> dict[str, RasterLayer]:
    result: dict[str, RasterLayer] = {}
    for name, path in paths.items():
        with rasterio.open(path) as dataset:
            result[name] = RasterLayer(dataset.read(1).astype("float64"), dataset.transform)
    return result


def _nearest(layer: RasterLayer, x: float, y: float) -> float:
    col, row = ~layer.transform * (x, y)
    column = int(math.floor(col))
    line = int(math.floor(row))
    if line < 0 or column < 0 or line >= layer.array.shape[0] or column >= layer.array.shape[1]:
        return float("nan")
    return float(layer.array[line, column])


def _bilinear(layer: RasterLayer, x: float, y: float) -> float:
    col_corner, row_corner = ~layer.transform * (x, y)
    col = col_corner - 0.5
    row = row_corner - 0.5
    col0, row0 = math.floor(col), math.floor(row)
    if row0 < 0 or col0 < 0 or row0 + 1 >= layer.array.shape[0] or col0 + 1 >= layer.array.shape[1]:
        return float("nan")
    values = layer.array[row0 : row0 + 2, col0 : col0 + 2]
    if not np.isfinite(values).all():
        return float("nan")
    dx, dy = col - col0, row - row0
    return float(
        values[0, 0] * (1 - dx) * (1 - dy)
        + values[0, 1] * dx * (1 - dy)
        + values[1, 0] * (1 - dx) * dy
        + values[1, 1] * dx * dy
    )


def _sample_point(layers: dict[str, RasterLayer], x: float, y: float) -> dict[str, float]:
    mean = _bilinear(layers["depth_mean_m"], x, y)
    if not np.isfinite(mean):
        return {name: float("nan") for name in layers}
    continuous = {"depth_mean_m", "depth_min_m", "depth_max_m", "depth_std_m"}
    return {
        name: (_bilinear(layer, x, y) if name in continuous else _nearest(layer, x, y))
        for name, layer in layers.items()
    }


def sample_bathymetry(
    transects: gpd.GeoDataFrame,
    prepared: PreparedBathymetry,
    settings: BathymetryConfig,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Find first valid water and sample at no finer than native resolution."""
    layers = _load_layers(prepared.paths)
    effective_spacing = max(
        settings.continuous_sample_spacing_m, prepared.native_effective_resolution_m
    )
    regular = np.arange(0.0, settings.maximum_offshore_distance_m + 0.01, effective_spacing)
    distances = sorted(
        {
            *[round(float(value), 6) for value in regular],
            *[float(value) for value in settings.target_distances_m],
            float(settings.maximum_offshore_distance_m),
        }
    )
    search_spacing = min(25.0, prepared.native_effective_resolution_m / 2)
    search = np.arange(0.0, settings.first_valid_search_max_distance_m + 0.01, search_spacing)
    sample_rows: list[dict[str, object]] = []
    origin_rows: dict[str, dict[str, object]] = {}
    for transect in transects.sort_values("bathymetry_transect_id").itertuples():
        first_distance = float("nan")
        first_values: dict[str, float] = {}
        for distance in search:
            point = transect.geometry.interpolate(float(distance))
            values = _sample_point(layers, point.x, point.y)
            if np.isfinite(values["depth_mean_m"]):
                first_distance = float(distance)
                first_values = values
                break
        if not np.isfinite(first_distance):
            status = "no_valid_bathymetry"
        elif first_distance <= prepared.native_effective_resolution_m / 2:
            status = "exact_or_near_coast"
        elif first_distance > settings.large_coastal_gap_threshold_m:
            status = "large_coastal_gap"
        else:
            status = "shifted_offshore"
        origin_rows[transect.bathymetry_transect_id] = {
            "first_valid_depth_distance_m": first_distance,
            "first_valid_depth_m": first_values.get("depth_mean_m", float("nan")),
            "first_valid_source_type": "unknown",
            "first_valid_interpolation_flag": first_values.get("interpolation_flag", float("nan")),
            "first_valid_quality_index": first_values.get("quality_index", float("nan")),
            "bathymetry_origin_status": status,
        }
        for distance in distances:
            point = transect.geometry.interpolate(distance)
            values = _sample_point(layers, point.x, point.y)
            valid = np.isfinite(values["depth_mean_m"])
            sample_rows.append(
                {
                    "bathymetry_transect_id": transect.bathymetry_transect_id,
                    "segment_id": transect.segment_id,
                    "distance_from_coast_m": distance,
                    "depth_mean_m": values["depth_mean_m"],
                    "depth_min_m": values.get("depth_min_m", float("nan")),
                    "depth_max_m": values.get("depth_max_m", float("nan")),
                    "depth_std_m": values.get("depth_std_m", float("nan")),
                    "observation_count": values.get("observation_count", float("nan")),
                    "source_reference": values.get("source_reference", float("nan")),
                    "source_type": "unknown",
                    "quality_index": values.get("quality_index", float("nan")),
                    "interpolation_flag": values.get("interpolation_flag", float("nan")),
                    "native_effective_resolution_m": prepared.native_effective_resolution_m,
                    "sample_valid": bool(valid),
                }
            )
    enriched = transects.copy()
    for field in (
        "first_valid_depth_distance_m",
        "first_valid_depth_m",
        "first_valid_source_type",
        "first_valid_interpolation_flag",
        "first_valid_quality_index",
        "bathymetry_origin_status",
    ):
        enriched[field] = enriched["bathymetry_transect_id"].map(
            {key: values[field] for key, values in origin_rows.items()}
        )
    return enriched, pd.DataFrame(sample_rows)
