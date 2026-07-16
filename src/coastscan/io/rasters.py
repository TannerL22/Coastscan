"""Elevation validation, reprojection, clipping, slope, and roughness preparation."""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import transform

from coastscan.catalog.manifests import sha256_file
from coastscan.exceptions import MissingInputError, RasterValidationError


@dataclass(frozen=True)
class PreparedTerrain:
    dem_path: Path
    slope_path: Path
    roughness_path: Path
    source_crs: str
    source_resolution: tuple[float, float]
    output_resolution: tuple[float, float]
    cache_key: str
    cache_used: bool


def _write_raster(path: Path, array: np.ndarray, profile: dict[str, object], nodata: float) -> None:
    output = np.where(np.isfinite(array), array, nodata).astype("float32")
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(output, 1)


def _window_sum(array: np.ndarray, radius: int) -> np.ndarray:
    size = 2 * radius + 1
    padded = np.pad(array, radius, mode="constant")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
    return cast(
        np.ndarray,
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size],
    )


def local_roughness(elevation: np.ndarray, window_pixels: int) -> np.ndarray:
    """Nodata-aware centred local elevation standard deviation."""
    radius = max(0, window_pixels // 2)
    if radius == 0:
        return np.where(np.isfinite(elevation), 0.0, np.nan)
    valid = np.isfinite(elevation)
    values = np.where(valid, elevation, 0.0)
    count = _window_sum(valid.astype(float), radius)
    total = _window_sum(values, radius)
    total_sq = _window_sum(values**2, radius)
    mean = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    variance = np.divide(total_sq, count, out=np.zeros_like(total), where=count > 0) - mean**2
    result = np.sqrt(np.maximum(variance, 0))
    result[(count == 0) | ~valid] = np.nan
    return cast(np.ndarray, result)


def slope_degrees(elevation: np.ndarray, pixel_x: float, pixel_y: float) -> np.ndarray:
    """Nodata-aware central finite-difference gradient expressed in degrees."""
    with np.errstate(invalid="ignore"):
        gradient_y, gradient_x = np.gradient(elevation, abs(pixel_y), abs(pixel_x))
        slope = np.degrees(np.arctan(np.hypot(gradient_x, gradient_y)))
    slope[~np.isfinite(elevation)] = np.nan
    return cast(np.ndarray, np.clip(slope, 0, 90))


def prepare_terrain(
    source_path: Path,
    land: Polygon | MultiPolygon,
    analysis_crs: str,
    output_dir: Path,
    roughness_window_m: float,
    *,
    force: bool = False,
) -> PreparedTerrain:
    """Validate, bilinearly reproject, clip, and derive deterministic terrain rasters."""
    if not source_path.is_file():
        raise MissingInputError(
            f"Missing required elevation raster: {source_path}\n"
            "Add the configured raster or update the region YAML."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    dem_path = output_dir / "dem_analysis_crs.tif"
    slope_path = output_dir / "slope_degrees.tif"
    roughness_path = output_dir / "roughness.tif"
    cache_path = output_dir / "terrain_cache.json"
    with rasterio.open(source_path) as source:
        if source.crs is None:
            raise RasterValidationError(f"Elevation raster has no CRS: {source_path}")
        if source.transform.is_identity or source.transform.a == 0 or source.transform.e == 0:
            raise RasterValidationError(
                f"Elevation raster has invalid affine transform: {source_path}"
            )
        source_crs = str(source.crs)
        source_resolution = (abs(float(source.res[0])), abs(float(source.res[1])))
        to_source = Transformer.from_crs(analysis_crs, source.crs, always_xy=True).transform
        source_land = transform(to_source, land)
        if not source_land.intersects(box(*source.bounds)):
            raise RasterValidationError(
                "Elevation raster does not overlap the configured land region"
            )
        cache_payload = {
            "source_checksum": sha256_file(source_path),
            "analysis_crs": analysis_crs,
            "land_bounds": [round(value, 3) for value in land.bounds],
            "roughness_window_m": roughness_window_m,
        }
        cache_key = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode()).hexdigest()[
            :16
        ]
        if (
            not force
            and all(path.is_file() for path in (dem_path, slope_path, roughness_path, cache_path))
            and json.loads(cache_path.read_text(encoding="utf-8")).get("cache_key") == cache_key
        ):
            with rasterio.open(dem_path) as cached:
                output_resolution = (abs(float(cached.res[0])), abs(float(cached.res[1])))
            return PreparedTerrain(
                dem_path,
                slope_path,
                roughness_path,
                source_crs,
                source_resolution,
                output_resolution,
                cache_key,
                True,
            )
        transform_options: dict[str, object] = {}
        if source.crs == rasterio.crs.CRS.from_user_input(analysis_crs):
            transform_options["resolution"] = source_resolution
        destination_transform, width, height = calculate_default_transform(
            source.crs,
            analysis_crs,
            source.width,
            source.height,
            *source.bounds,
            **transform_options,
        )
        destination = np.full((height, width), np.nan, dtype="float32")
        source_array = source.read(1, masked=True).filled(np.nan).astype("float32")
        reproject(
            source_array,
            destination,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=np.nan,
            dst_transform=destination_transform,
            dst_crs=analysis_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    buffer_m = max(roughness_window_m, 10.0)
    min_x, min_y, max_x, max_y = land.buffer(buffer_m).bounds
    pixel_x, pixel_y = abs(destination_transform.a), abs(destination_transform.e)
    col_min = max(0, math.floor((min_x - destination_transform.c) / pixel_x))
    col_max = min(width, math.ceil((max_x - destination_transform.c) / pixel_x))
    row_min = max(0, math.floor((destination_transform.f - max_y) / pixel_y))
    row_max = min(height, math.ceil((destination_transform.f - min_y) / pixel_y))
    if row_min >= row_max or col_min >= col_max:
        raise RasterValidationError("Computed DEM clip window is empty")
    destination = destination[row_min:row_max, col_min:col_max]
    clipped_transform = from_origin(
        destination_transform.c + col_min * pixel_x,
        destination_transform.f - row_min * pixel_y,
        pixel_x,
        pixel_y,
    )
    slope = slope_degrees(destination, pixel_x, pixel_y)
    window_pixels = max(1, round(roughness_window_m / ((pixel_x + pixel_y) / 2)))
    roughness = local_roughness(destination, window_pixels)
    nodata = -9999.0
    profile: dict[str, object] = {
        "driver": "GTiff",
        "height": destination.shape[0],
        "width": destination.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": analysis_crs,
        "transform": clipped_transform,
        "nodata": nodata,
        "compress": "deflate",
        "predictor": 3,
    }
    _write_raster(dem_path, destination, profile, nodata)
    _write_raster(slope_path, slope, profile, nodata)
    _write_raster(roughness_path, roughness, profile, nodata)
    cache_path.write_text(
        json.dumps({"cache_key": cache_key, **cache_payload}, indent=2), encoding="utf-8"
    )
    return PreparedTerrain(
        dem_path,
        slope_path,
        roughness_path,
        source_crs,
        source_resolution,
        (pixel_x, pixel_y),
        cache_key,
        False,
    )
