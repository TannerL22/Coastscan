"""Scene preparation and segment-zone optical observation extraction."""

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import reproject

from coastscan.optical.indices import regional_percentiles, summarise_components
from coastscan.optical.masks import build_masks, validity_reason
from coastscan.optical.radiometry import Radiometry, reflectance, resampling_for_asset
from coastscan.optical.texture import texture_strength


def _aligned(
    path: Path,
    reference: rasterio.DatasetReader,
    *,
    categorical: bool,
    scale: float,
    offset: float,
    nodata: float | int | None,
) -> np.ndarray:
    with rasterio.open(path) as source:
        destination = np.full((reference.height, reference.width), np.nan, dtype="float32")
        reproject(
            source.read(1),
            destination,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=reference.transform,
            dst_crs=reference.crs,
            dst_nodata=np.nan,
            resampling=resampling_for_asset(categorical=categorical),
        )
    if categorical:
        return np.nan_to_num(destination, nan=0).astype("uint8")
    return reflectance(destination, Radiometry(scale=scale, offset=offset, nodata=nodata))


def scene_observations(
    scene: pd.Series,
    zones: gpd.GeoDataFrame,
    cache_directory: Path,
    settings: Any,
) -> list[dict[str, object]]:
    scene_dir = cache_directory / str(scene.scene_id)
    metadata = json.loads(str(scene.asset_checksums_or_etags))
    with rasterio.open(scene_dir / "blue.tif") as reference:
        arrays: dict[str, np.ndarray] = {}
        for role in ("blue", "green", "red", "nir", "swir1", "scene_classification"):
            details = metadata[role]
            arrays[role] = _aligned(
                scene_dir / f"{'scl' if role == 'scene_classification' else role}.tif",
                reference,
                categorical=role == "scene_classification",
                scale=float(details.get("scale") if details.get("scale") is not None else 0.0001),
                offset=float(details.get("offset") if details.get("offset") is not None else 0.0),
                nodata=details.get("nodata"),
            )
        masks = build_masks(
            arrays["blue"],
            arrays["green"],
            arrays["red"],
            arrays["nir"],
            arrays["swir1"],
            arrays["scene_classification"].astype("uint8"),
        )
        projected = zones.to_crs(reference.crs)
        transform = reference.transform
        shape = (reference.height, reference.width)
    records: list[dict[str, object]] = []
    for zone in projected.itertuples():
        base: dict[str, object] = {
            "segment_id": str(zone.segment_id),
            "zone_id": str(zone.zone_id),
            "zone_type": str(zone.zone_type),
            "scene_id": str(scene.scene_id),
            "acquisition_datetime_utc": str(scene.acquisition_datetime_utc),
            "year": int(scene.year),
            "month": int(scene.month),
            "processing_baseline": str(scene.processing_baseline),
        }
        if zone.geometry is None or zone.geometry.is_empty or str(zone.zone_status) != "valid":
            base.update(
                valid=False,
                invalid_reason=str(zone.zone_status),
                valid_pixel_count=0,
                zone_pixel_count=0,
                valid_pixel_share=0.0,
            )
            records.append(base)
            continue
        zone_pixels = geometry_mask(
            [zone.geometry], out_shape=shape, transform=transform, invert=True
        )
        reason = validity_reason(
            masks, zone_pixels, settings.masks.minimum_valid_water_pixels_per_zone
        )
        shares = masks.shares(zone_pixels)
        valid_pixels = zone_pixels & masks.valid_water
        valid_count = int(valid_pixels.sum())
        zone_count = int(zone_pixels.sum())
        share = valid_count / zone_count if zone_count else 0.0
        burdens_ok = (
            shares["cloud_share"] + shares["cirrus_share"]
            <= settings.masks.maximum_cloud_excluded_share
            and shares["shadow_share"] + shares["dark_shadow_share"]
            <= settings.masks.maximum_shadow_excluded_share
            and shares["glint_risk_share"] <= settings.masks.maximum_glint_excluded_share
            and shares["land_share"] <= settings.masks.maximum_land_excluded_share
            and shares["whitewater_share"] <= settings.masks.maximum_whitewater_excluded_share
        )
        valid = (
            reason == "valid"
            and share >= settings.masks.minimum_valid_zone_pixel_share
            and burdens_ok
        )
        if reason == "valid" and share < settings.masks.minimum_valid_zone_pixel_share:
            reason = "insufficient_valid_pixel_share"
        elif reason == "valid" and not burdens_ok:
            reason = "mask_burden_above_threshold"
        base.update(
            valid=valid,
            invalid_reason="valid" if valid else reason,
            valid_pixel_count=valid_count,
            zone_pixel_count=zone_count,
            valid_pixel_share=share,
            cloud_excluded_share=shares["cloud_share"] + shares["cirrus_share"],
            shadow_excluded_share=shares["shadow_share"] + shares["dark_shadow_share"],
            glint_excluded_share=shares["glint_risk_share"],
            land_excluded_share=shares["land_share"],
            whitewater_excluded_share=shares["whitewater_share"],
            **summarise_components(
                arrays["blue"], arrays["green"], arrays["red"], arrays["nir"], valid_pixels
            ),
            apparent_texture_strength=texture_strength(arrays["green"], valid_pixels),
        )
        records.append(base)
    return records


def extract_observations(
    scenes: pd.DataFrame,
    zones: gpd.GeoDataFrame,
    cache_directory: Path,
    settings: Any,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for _, scene in (
        scenes.loc[scenes.selected].sort_values(["acquisition_datetime_utc", "scene_id"]).iterrows()
    ):
        records.extend(scene_observations(scene, zones, cache_directory, settings))
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    ranked = regional_percentiles(
        frame, minimum_population=settings.clarity.minimum_regional_population
    )
    ranked["apparent_texture_percentile"] = (
        ranked.groupby(["scene_id", "zone_type"], sort=True).apparent_texture_strength.rank(
            method="average", pct=True
        )
        * 100.0
    )
    ranked["apparent_bottom_texture_candidate"] = (
        ranked.valid.astype(bool)
        & (ranked.clarity_percentile >= settings.clarity.clear_percentile_threshold)
        & (ranked.apparent_texture_percentile >= 75)
        & (ranked.glint_excluded_share <= settings.masks.maximum_glint_excluded_share)
        & (ranked.whitewater_excluded_share <= settings.masks.maximum_whitewater_excluded_share)
    )
    ranked.loc[
        ranked.valid_clarity_component_count < settings.clarity.minimum_valid_components,
        ["valid", "clarity_percentile"],
    ] = [False, np.nan]
    limited = ranked.invalid_reason.eq("valid") & ~ranked.valid.astype(bool)
    ranked.loc[limited, "invalid_reason"] = "insufficient_regional_components"
    return ranked.sort_values(["scene_id", "segment_id", "zone_type"]).reset_index(drop=True)
