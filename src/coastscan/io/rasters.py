"""Windowed multi-tile terrain validation, mosaicking, caching, and derivatives."""

import hashlib
import json
import math
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject
from rasterio.windows import Window
from rasterio.windows import transform as window_transform
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from shapely.ops import transform

from coastscan.catalog.manifests import sha256_file
from coastscan.exceptions import MissingInputError, RasterValidationError


@dataclass(frozen=True)
class RasterTileInfo:
    path: Path
    crs: str
    resolution: tuple[float, float]
    nodata: float | None
    bounds: tuple[float, float, float, float]
    width: int
    height: int
    file_size_bytes: int
    vertical_units: str


@dataclass(frozen=True)
class PreparedTerrain:
    dem_path: Path
    slope_path: Path
    roughness_path: Path
    mosaic_descriptor_path: Path
    source_crs: str
    source_resolution: tuple[float, float]
    output_resolution: tuple[float, float]
    cache_key: str
    cache_used: bool
    available_tile_count: int
    selected_tile_count: int
    selected_paths: tuple[Path, ...]
    selected_file_size_bytes: int
    selected_uncompressed_bytes: int
    clipped_dimensions: tuple[int, int]
    clipped_uncompressed_bytes: int
    cache_creation_seconds: float


def resolve_raster_paths(config: Any, root: Path) -> list[Path]:
    """Resolve a validated path, path list, directory, or glob deterministically."""
    if config.path is not None:
        candidates = [config.path]
    elif config.paths is not None:
        candidates = list(config.paths)
    elif config.directory is not None:
        directory = config.directory if config.directory.is_absolute() else root / config.directory
        candidates = sorted(
            [*directory.glob("*.tif"), *directory.glob("*.tiff")],
            key=lambda path: str(path).lower(),
        )
    else:
        pattern = str(config.glob)
        candidates = sorted(root.glob(pattern), key=lambda path: str(path).lower())
    resolved = [path if path.is_absolute() else root / path for path in candidates]
    unique = sorted(set(path.resolve() for path in resolved), key=lambda path: str(path).lower())
    if not unique:
        raise MissingInputError(
            "No elevation rasters matched the configured path source. "
            "Run acquire-region-data or update inputs.elevation."
        )
    missing = [path for path in unique if not path.is_file()]
    if missing:
        raise MissingInputError(
            "Missing required elevation raster(s):\n" + "\n".join(str(path) for path in missing)
        )
    return unique


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
    """Nodata-aware centred local population standard deviation."""
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


def inspect_raster_tiles(
    paths: list[Path],
    clip_geometry: Polygon | MultiPolygon,
    analysis_crs: str,
    configured_vertical_units: str,
) -> tuple[list[RasterTileInfo], list[RasterTileInfo]]:
    """Inspect headers only, validate consistency, and select spatially intersecting tiles."""
    available: list[RasterTileInfo] = []
    selected: list[RasterTileInfo] = []
    for path in sorted(paths, key=lambda value: str(value).lower()):
        with rasterio.open(path) as source:
            if source.crs is None:
                raise RasterValidationError(f"Elevation raster has no CRS: {path}")
            if source.transform.is_identity or source.transform.a == 0 or source.transform.e == 0:
                raise RasterValidationError(
                    f"Elevation raster has invalid affine transform: {path}"
                )
            tags = {**source.tags(), **source.tags(1)}
            vertical_units = str(
                tags.get("VERTICAL_UNITS") or tags.get("UNITTYPE") or configured_vertical_units
            ).strip()
            info = RasterTileInfo(
                path=path,
                crs=str(source.crs),
                resolution=(abs(float(source.res[0])), abs(float(source.res[1]))),
                nodata=float(source.nodata) if source.nodata is not None else None,
                bounds=(
                    float(source.bounds.left),
                    float(source.bounds.bottom),
                    float(source.bounds.right),
                    float(source.bounds.top),
                ),
                width=source.width,
                height=source.height,
                file_size_bytes=path.stat().st_size,
                vertical_units=vertical_units,
            )
            available.append(info)
            to_source = Transformer.from_crs(analysis_crs, source.crs, always_xy=True).transform
            source_clip = transform(to_source, clip_geometry)
            if source_clip.intersects(box(*source.bounds)):
                selected.append(info)
    if not selected:
        raise RasterValidationError("No configured elevation tile overlaps the processing corridor")
    crs_values = {info.crs for info in selected}
    if len(crs_values) != 1:
        raise RasterValidationError(
            "Selected elevation tiles have mixed CRS values: " + ", ".join(sorted(crs_values))
        )
    reference_resolution = selected[0].resolution
    inconsistent = [
        info.path
        for info in selected
        if not np.allclose(info.resolution, reference_resolution, rtol=1e-6, atol=1e-6)
    ]
    if inconsistent:
        raise RasterValidationError(
            "Selected elevation tiles have materially inconsistent pixel resolutions: "
            + ", ".join(str(path) for path in inconsistent)
        )
    units = {info.vertical_units.lower() for info in selected}
    if len(units) != 1:
        raise RasterValidationError(
            "Selected elevation tiles have mixed vertical units: " + ", ".join(sorted(units))
        )
    return available, selected


def _windows(width: int, height: int, block_size: int = 512) -> list[Window]:
    return [
        Window(column, row, min(block_size, width - column), min(block_size, height - row))
        for row in range(0, height, block_size)
        for column in range(0, width, block_size)
    ]


def _write_windowed_mosaic(
    selected: list[RasterTileInfo],
    clip_geometry: Polygon | MultiPolygon,
    analysis_crs: str,
    destination_path: Path,
    output_resolution: tuple[float, float],
) -> tuple[int, int]:
    pixel_x, pixel_y = output_resolution
    min_x, min_y, max_x, max_y = clip_geometry.bounds
    left = math.floor(min_x / pixel_x) * pixel_x
    right = math.ceil(max_x / pixel_x) * pixel_x
    bottom = math.floor(min_y / pixel_y) * pixel_y
    top = math.ceil(max_y / pixel_y) * pixel_y
    width = int(round((right - left) / pixel_x))
    height = int(round((top - bottom) / pixel_y))
    if width <= 0 or height <= 0:
        raise RasterValidationError("Computed clipped terrain grid is empty")
    destination_transform = from_origin(left, top, pixel_x, pixel_y)
    nodata = -9999.0
    profile: dict[str, object] = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": analysis_crs,
        "transform": destination_transform,
        "nodata": nodata,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with ExitStack() as stack:
        sources = [stack.enter_context(rasterio.open(info.path)) for info in selected]
        destination = stack.enter_context(rasterio.open(destination_path, "w", **profile))
        for window in _windows(width, height):
            shape = (int(window.height), int(window.width))
            transform_for_window = window_transform(window, destination_transform)
            merged = np.full(shape, np.nan, dtype="float32")
            for source in sources:
                candidate = np.full(shape, np.nan, dtype="float32")
                reproject(
                    source=rasterio.band(source, 1),
                    destination=candidate,
                    src_transform=source.transform,
                    src_crs=source.crs,
                    src_nodata=source.nodata,
                    dst_transform=transform_for_window,
                    dst_crs=analysis_crs,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                    init_dest_nodata=True,
                )
                fill = ~np.isfinite(merged) & np.isfinite(candidate)
                merged[fill] = candidate[fill]
            inside = geometry_mask(
                [mapping(clip_geometry)],
                out_shape=shape,
                transform=transform_for_window,
                invert=True,
            )
            merged[~inside] = np.nan
            destination.write(np.where(np.isfinite(merged), merged, nodata), 1, window=window)
    return width, height


def _derive_rasters_blockwise(
    dem_path: Path,
    slope_path: Path,
    roughness_path: Path,
    roughness_window_m: float,
) -> None:
    with rasterio.open(dem_path) as dem:
        profile = dem.profile.copy()
        pixel_x, pixel_y = abs(float(dem.res[0])), abs(float(dem.res[1]))
        window_pixels = max(1, round(roughness_window_m / ((pixel_x + pixel_y) / 2)))
        radius = max(1, window_pixels // 2)
        with (
            rasterio.open(slope_path, "w", **profile) as slope_output,
            rasterio.open(roughness_path, "w", **profile) as roughness_output,
        ):
            for core in _windows(dem.width, dem.height):
                row_start = max(0, int(core.row_off) - radius)
                column_start = max(0, int(core.col_off) - radius)
                row_stop = min(dem.height, int(core.row_off + core.height) + radius)
                column_stop = min(dem.width, int(core.col_off + core.width) + radius)
                expanded = Window(
                    column_start,
                    row_start,
                    column_stop - column_start,
                    row_stop - row_start,
                )
                elevation = dem.read(1, window=expanded, masked=True).filled(np.nan).astype(float)
                slope = slope_degrees(elevation, pixel_x, pixel_y)
                roughness = local_roughness(elevation, window_pixels)
                row_offset = int(core.row_off) - row_start
                column_offset = int(core.col_off) - column_start
                core_rows = slice(row_offset, row_offset + int(core.height))
                core_columns = slice(column_offset, column_offset + int(core.width))
                nodata = float(dem.nodata)
                slope_core = slope[core_rows, core_columns]
                roughness_core = roughness[core_rows, core_columns]
                slope_output.write(
                    np.where(np.isfinite(slope_core), slope_core, nodata).astype("float32"),
                    1,
                    window=core,
                )
                roughness_output.write(
                    np.where(np.isfinite(roughness_core), roughness_core, nodata).astype("float32"),
                    1,
                    window=core,
                )


def prepare_terrain(
    source_paths: list[Path],
    clip_geometry: Polygon | MultiPolygon,
    analysis_crs: str,
    output_dir: Path,
    roughness_window_m: float,
    *,
    vertical_units: str = "metres",
    force: bool = False,
) -> PreparedTerrain:
    """Build a clipped deterministic virtual mosaic and blockwise derivatives."""
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    dem_path = output_dir / "dem_analysis_crs.tif"
    slope_path = output_dir / "slope_degrees.tif"
    roughness_path = output_dir / "roughness.tif"
    descriptor_path = output_dir / "dem_mosaic.vrt.json"
    cache_path = output_dir / "terrain_cache.json"
    available, selected = inspect_raster_tiles(
        source_paths, clip_geometry, analysis_crs, vertical_units
    )
    source_crs = selected[0].crs
    source_resolution = selected[0].resolution
    if rasterio.crs.CRS.from_user_input(source_crs) == rasterio.crs.CRS.from_user_input(
        analysis_crs
    ):
        output_resolution = source_resolution
    else:
        first = selected[0]
        transform_result, _, _ = calculate_default_transform(
            source_crs,
            analysis_crs,
            first.width,
            first.height,
            *first.bounds,
        )
        output_resolution = (abs(float(transform_result.a)), abs(float(transform_result.e)))
    checksums = {str(info.path): sha256_file(info.path) for info in selected}
    cache_payload: dict[str, object] = {
        "source_checksums": checksums,
        "analysis_crs": analysis_crs,
        "clip_geometry_sha256": hashlib.sha256(clip_geometry.wkb).hexdigest(),
        "roughness_window_m": roughness_window_m,
        "output_resolution": output_resolution,
        "overlap_rule": "sorted_path_first_valid_pixel",
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    cache_used = (
        not force
        and all(
            path.is_file()
            for path in (dem_path, slope_path, roughness_path, descriptor_path, cache_path)
        )
        and json.loads(cache_path.read_text(encoding="utf-8")).get("cache_key") == cache_key
    )
    if cache_used:
        with rasterio.open(dem_path) as cached:
            clipped_dimensions = (cached.width, cached.height)
        creation_seconds = 0.0
    else:
        clipped_dimensions = _write_windowed_mosaic(
            selected, clip_geometry, analysis_crs, dem_path, output_resolution
        )
        _derive_rasters_blockwise(dem_path, slope_path, roughness_path, roughness_window_m)
        descriptor = {
            "type": "rasterio_windowed_virtual_mosaic",
            "analysis_crs": analysis_crs,
            "resolution": list(output_resolution),
            "selected_tiles": [str(info.path) for info in selected],
            "source_checksums": checksums,
            "overlap_rule": "sorted_path_first_valid_pixel",
            "clip_bounds": list(clip_geometry.bounds),
        }
        descriptor_path.write_text(
            json.dumps(descriptor, indent=2, sort_keys=True), encoding="utf-8"
        )
        cache_path.write_text(
            json.dumps({"cache_key": cache_key, **cache_payload}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        creation_seconds = time.perf_counter() - started
    selected_file_size = sum(info.file_size_bytes for info in selected)
    selected_uncompressed = sum(info.width * info.height * 4 for info in selected)
    clipped_uncompressed = clipped_dimensions[0] * clipped_dimensions[1] * 4
    return PreparedTerrain(
        dem_path=dem_path,
        slope_path=slope_path,
        roughness_path=roughness_path,
        mosaic_descriptor_path=descriptor_path,
        source_crs=source_crs,
        source_resolution=source_resolution,
        output_resolution=output_resolution,
        cache_key=cache_key,
        cache_used=cache_used,
        available_tile_count=len(available),
        selected_tile_count=len(selected),
        selected_paths=tuple(info.path for info in selected),
        selected_file_size_bytes=selected_file_size,
        selected_uncompressed_bytes=selected_uncompressed,
        clipped_dimensions=clipped_dimensions,
        clipped_uncompressed_bytes=clipped_uncompressed,
        cache_creation_seconds=creation_seconds,
    )
