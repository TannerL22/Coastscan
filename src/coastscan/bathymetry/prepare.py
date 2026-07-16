"""Cached preparation of aligned canonical bathymetry rasters."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject, transform_bounds

from coastscan.bathymetry.adapters import ADAPTER_VERSION, canonicalize_depth
from coastscan.catalog.manifests import sha256_file, sha256_text
from coastscan.config import data_path
from coastscan.exceptions import MissingInputError, RasterValidationError
from coastscan.models.region import RegionConfig


@dataclass(frozen=True)
class PreparedBathymetry:
    cache_key: str
    cache_used: bool
    paths: dict[str, Path]
    source_path: Path
    source_checksum: str
    source_crs: str
    source_resolution: tuple[float, float]
    native_effective_resolution_m: float
    output_resolution: tuple[float, float]
    bounds: tuple[float, float, float, float]
    dimensions: tuple[int, int]
    variables: dict[str, str]
    nodata: dict[str, float | None]


def _subdataset(path: Path, variable: str) -> str:
    with rasterio.open(path) as root:
        matches = [item for item in root.subdatasets if item.rsplit(":", 1)[-1] == variable]
        if not root.subdatasets and variable in {"band_1", "1", "depth"} and root.count == 1:
            return str(path)
    if not matches:
        raise RasterValidationError(f"Bathymetry variable {variable!r} is absent from {path}")
    return str(matches[0])


def _canonical_mapping(config: RegionConfig) -> dict[str, str]:
    source = config.inputs.bathymetry
    assert source is not None
    raw = source.variables.model_dump()
    names = {
        "mean_depth": "depth_mean_m",
        "minimum_depth": "depth_min_m",
        "maximum_depth": "depth_max_m",
        "standard_deviation": "depth_std_m",
        "observation_count": "observation_count",
        "interpolation_flag": "interpolation_flag",
        "source_reference": "source_reference",
        "quality_index": "quality_index",
    }
    return {names[key]: value for key, value in raw.items() if value is not None}


def _transect_origin_count(length: float, spacing: float) -> int:
    origins = np.arange(0.0, length, spacing)
    include_end = not len(origins) or length - origins[-1] > spacing * 0.5
    return len(origins) + int(include_end)


def bathymetry_cache_key(config: RegionConfig, segments: gpd.GeoDataFrame, root: Path) -> str:
    """Return the deterministic current cache key without creating cache files."""
    source = config.inputs.bathymetry
    settings = config.bathymetry
    assert source is not None and settings is not None
    path = data_path(source.path, root)
    mapping = _canonical_mapping(config)
    relevant = {
        "source_checksum": sha256_file(path),
        "adapter": source.source_adapter,
        "adapter_version": ADAPTER_VERSION,
        "mapping": mapping,
        "segments_bounds": [round(float(value), 3) for value in segments.total_bounds],
        "upstream_segments": sha256_text(
            "\n".join(
                f"{segment_id}:{geometry.hex()}"
                for segment_id, geometry in zip(
                    segments.segment_id.astype(str), segments.geometry.to_wkb(), strict=True
                )
            )
        ),
        "analysis_crs": config.analysis_crs,
        "settings": settings.model_dump(mode="json"),
        "native_resolution_m": source.native_resolution_m,
        "sign": source.depth_sign_convention,
        "zero_is_valid": source.zero_is_valid,
    }
    return sha256_text(json.dumps(relevant, sort_keys=True))[:16]


def valid_bathymetry_cache_exists(
    config: RegionConfig, segments: gpd.GeoDataFrame, root: Path
) -> bool:
    key = bathymetry_cache_key(config, segments, root)
    cache_dir = root / "data" / "interim" / config.region_id / "bathymetry_cache" / key
    mapping = _canonical_mapping(config)
    return (cache_dir / "metadata.json").is_file() and all(
        (cache_dir / f"{name}.tif").is_file() for name in mapping
    )


def _source_metadata(
    path: Path, mean_variable: str
) -> tuple[str, Affine, tuple[float, ...], dict[str, Any]]:
    with rasterio.open(_subdataset(path, mean_variable)) as dataset:
        tags = dataset.tags()
        crs = dataset.crs or tags.get("NC_GLOBAL#grid_mapping_epsg_code")
        if not crs:
            raise RasterValidationError("Bathymetry CRS is missing from source metadata")
        transform = dataset.transform
        bounds = tuple(dataset.bounds)
        if transform.is_identity:
            raise RasterValidationError("Bathymetry geotransform is missing")
        metadata = {
            "width": dataset.width,
            "height": dataset.height,
            "dtype": dataset.dtypes[0],
            "nodata": dataset.nodata,
            "tags": tags,
        }
    return str(crs), transform, bounds, metadata


def inspect_bathymetry_source(
    config: RegionConfig, segments: gpd.GeoDataFrame, root: Path
) -> dict[str, Any]:
    source = config.inputs.bathymetry
    settings = config.bathymetry
    if source is None or settings is None:
        raise RasterValidationError("Bathymetry is not configured for this region")
    path = data_path(source.path, root)
    if not path.is_file():
        raise MissingInputError(f"Bathymetry source not found: {path}")
    mapping = _canonical_mapping(config)
    crs, transform, bounds, metadata = _source_metadata(path, source.variables.mean_depth)
    source_bounds_analysis = transform_bounds(crs, config.analysis_crs, *bounds, densify_pts=21)
    corridor = tuple(segments.total_bounds)
    buffer_m = settings.maximum_offshore_distance_m
    corridor_buffered = (
        corridor[0] - buffer_m,
        corridor[1] - buffer_m,
        corridor[2] + buffer_m,
        corridor[3] + buffer_m,
    )
    overlap = not (
        source_bounds_analysis[2] < corridor_buffered[0]
        or source_bounds_analysis[0] > corridor_buffered[2]
        or source_bounds_analysis[3] < corridor_buffered[1]
        or source_bounds_analysis[1] > corridor_buffered[3]
    )
    return {
        "source_id": source.source_id,
        "product_release": source.source_release,
        "path": str(path),
        "checksum": sha256_file(path),
        "source_adapter": source.source_adapter,
        "source_crs": crs,
        "source_transform": list(transform)[:6],
        "source_bounds": list(bounds),
        "native_resolution_degrees": [abs(transform.a), abs(transform.e)],
        "native_effective_resolution_m": source.native_resolution_m,
        "vertical_datum": source.vertical_datum,
        "depth_sign_convention": source.depth_sign_convention,
        "variables": mapping,
        "nodata": metadata["nodata"],
        "dimensions": [metadata["width"], metadata["height"]],
        "aoi_coverage": overlap,
        "source_reference_available": "source_reference" in mapping,
        "quality_layer_available": "quality_index" in mapping,
        "higher_resolution_source_assessment": source.higher_resolution_assessment,
        "estimated_bathymetry_transect_count": int(
            sum(
                _transect_origin_count(float(length), settings.transect_spacing_m)
                for length in segments.loc[
                    (segments.orientation_status != "ambiguous")
                    & np.isfinite(segments.seaward_bearing_deg),
                    "geometry",
                ].length
            )
        ),
    }


def prepare_bathymetry(
    config: RegionConfig,
    segments: gpd.GeoDataFrame,
    root: Path,
    *,
    force: bool = False,
) -> PreparedBathymetry:
    source = config.inputs.bathymetry
    settings = config.bathymetry
    assert source is not None and settings is not None
    path = data_path(source.path, root)
    if not path.is_file():
        raise MissingInputError(f"Bathymetry source not found: {path}")
    mapping = _canonical_mapping(config)
    source_crs, src_transform, src_bounds, _ = _source_metadata(path, source.variables.mean_depth)
    source_checksum = sha256_file(path)
    cache_key = bathymetry_cache_key(config, segments, root)
    cache_dir = root / "data" / "interim" / config.region_id / "bathymetry_cache" / cache_key
    metadata_path = cache_dir / "metadata.json"
    paths = {name: cache_dir / f"{name}.tif" for name in mapping}
    if not force and metadata_path.is_file() and all(item.is_file() for item in paths.values()):
        saved = json.loads(metadata_path.read_text(encoding="utf-8"))
        return PreparedBathymetry(
            cache_key=cache_key,
            cache_used=True,
            paths=paths,
            source_path=path,
            source_checksum=source_checksum,
            source_crs=source_crs,
            source_resolution=tuple(saved["source_resolution"]),
            native_effective_resolution_m=source.native_resolution_m,
            output_resolution=tuple(saved["output_resolution"]),
            bounds=tuple(saved["bounds"]),
            dimensions=tuple(saved["dimensions"]),
            variables=mapping,
            nodata=saved["nodata"],
        )

    output_res = source.native_resolution_m
    corridor = segments.geometry.union_all().envelope.buffer(
        settings.maximum_offshore_distance_m + output_res
    )
    left, bottom, right, top = corridor.bounds
    left = math.floor(left / output_res) * output_res
    bottom = math.floor(bottom / output_res) * output_res
    right = math.ceil(right / output_res) * output_res
    top = math.ceil(top / output_res) * output_res
    width = int(round((right - left) / output_res))
    height = int(round((top - bottom) / output_res))
    dst_transform = Affine(output_res, 0, left, 0, -output_res, top)
    source_bounds_analysis = transform_bounds(
        source_crs, config.analysis_crs, *src_bounds, densify_pts=21
    )
    if (
        right < source_bounds_analysis[0]
        or left > source_bounds_analysis[2]
        or top < source_bounds_analysis[1]
        or bottom > source_bounds_analysis[3]
    ):
        raise RasterValidationError("Bathymetry source does not overlap the offshore corridor")
    cache_dir.mkdir(parents=True, exist_ok=True)
    nodata: dict[str, float | None] = {}
    continuous = {"depth_mean_m", "depth_min_m", "depth_max_m", "depth_std_m"}
    depth_layers = {"depth_mean_m", "depth_min_m", "depth_max_m"}
    for canonical, variable in mapping.items():
        with rasterio.open(_subdataset(path, variable)) as dataset:
            array = dataset.read(1, masked=True).astype("float64").filled(np.nan)
            if canonical in depth_layers:
                array = canonicalize_depth(
                    array,
                    source.depth_sign_convention,
                    zero_is_valid=source.zero_is_valid,
                )
            elif canonical == "depth_std_m":
                array[~np.isfinite(array) | (array < 0)] = np.nan
            elif dataset.nodata is not None and np.isfinite(dataset.nodata):
                array[array == dataset.nodata] = np.nan
            destination = np.full((height, width), np.nan, dtype="float32")
            reproject(
                source=array,
                destination=destination,
                src_transform=src_transform,
                src_crs=source_crs,
                src_nodata=np.nan,
                dst_transform=dst_transform,
                dst_crs=config.analysis_crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear if canonical in continuous else Resampling.nearest,
            )
        with rasterio.open(
            paths[canonical],
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="float32",
            crs=config.analysis_crs,
            transform=dst_transform,
            nodata=np.nan,
            compress="deflate",
            tiled=True,
        ) as output:
            output.write(destination, 1)
            output.update_tags(
                canonical_variable=canonical,
                source_variable=variable,
                source_id=source.source_id,
                source_release=source.source_release,
                vertical_datum=source.vertical_datum,
                native_effective_resolution_m=source.native_resolution_m,
                source_adapter_version=ADAPTER_VERSION,
            )
        nodata[canonical] = None
    saved = {
        "cache_key": cache_key,
        "source_resolution": [abs(src_transform.a), abs(src_transform.e)],
        "output_resolution": [output_res, output_res],
        "bounds": [left, bottom, right, top],
        "dimensions": [width, height],
        "nodata": nodata,
        "variable_mapping": mapping,
    }
    metadata_path.write_text(json.dumps(saved, indent=2, sort_keys=True), encoding="utf-8")
    return PreparedBathymetry(
        cache_key=cache_key,
        cache_used=False,
        paths=paths,
        source_path=path,
        source_checksum=source_checksum,
        source_crs=source_crs,
        source_resolution=(abs(src_transform.a), abs(src_transform.e)),
        native_effective_resolution_m=source.native_resolution_m,
        output_resolution=(output_res, output_res),
        bounds=(left, bottom, right, top),
        dimensions=(width, height),
        variables=mapping,
        nodata=nodata,
    )
