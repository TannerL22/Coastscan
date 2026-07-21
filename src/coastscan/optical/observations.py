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

from coastscan.optical.indices import blue_green_ratio, ndti, regional_percentiles
from coastscan.optical.masks import build_masks, validity_reason_at
from coastscan.optical.radiometry import Radiometry, reflectance, resampling_for_asset
from coastscan.optical.texture import texture_magnitude


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
        source_values = np.array(source.read(1), copy=True)
        reproject(
            source_values,
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


def load_scene_arrays(scene: pd.Series, cache_directory: Path) -> dict[str, np.ndarray]:
    """Load and radiometrically align a scene to its native 10 m blue-band grid."""
    scene_dir = cache_directory / str(scene.scene_id)
    metadata = json.loads(str(scene.asset_checksums_or_etags))
    arrays: dict[str, np.ndarray] = {}
    with rasterio.open(scene_dir / "blue.tif") as reference:
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
    return arrays


GridKey = tuple[int, int, str, tuple[float, ...]]
ZoneGrid = tuple[gpd.GeoDataFrame, dict[str, np.ndarray]]


def _grid_key(reference: rasterio.DatasetReader) -> GridKey:
    return (
        reference.height,
        reference.width,
        str(reference.crs),
        tuple(float(value) for value in reference.transform),
    )


def _prepare_zone_grid(zones: gpd.GeoDataFrame, reference: rasterio.DatasetReader) -> ZoneGrid:
    projected = zones.to_crs(reference.crs)
    shape = (reference.height, reference.width)
    indices: dict[str, np.ndarray] = {}
    for zone in projected.itertuples():
        zone_id = str(zone.zone_id)
        if zone.geometry is None or zone.geometry.is_empty or str(zone.zone_status) != "valid":
            indices[zone_id] = np.array([], dtype=np.int64)
            continue
        zone_mask = geometry_mask(
            [zone.geometry], out_shape=shape, transform=reference.transform, invert=True
        )
        indices[zone_id] = np.flatnonzero(zone_mask).astype(np.int64, copy=False)
    return projected, indices


def _share_at(mask: np.ndarray, indices: np.ndarray) -> float:
    return float(np.mean(mask.ravel()[indices])) if len(indices) else 0.0


def _median_at(values: np.ndarray, indices: np.ndarray) -> float:
    selected = values.ravel()[indices]
    finite = selected[np.isfinite(selected)]
    return float(np.median(finite)) if finite.size else float("nan")


def scene_observations(
    scene: pd.Series,
    zones: gpd.GeoDataFrame,
    cache_directory: Path,
    settings: Any,
    zone_grid_cache: dict[GridKey, ZoneGrid] | None = None,
) -> list[dict[str, object]]:
    arrays = load_scene_arrays(scene, cache_directory)
    scene_dir = cache_directory / str(scene.scene_id)
    with rasterio.open(scene_dir / "blue.tif") as reference:
        masks = build_masks(
            arrays["blue"],
            arrays["green"],
            arrays["red"],
            arrays["nir"],
            arrays["swir1"],
            arrays["scene_classification"].astype("uint8"),
        )
        key = _grid_key(reference)
        cache = zone_grid_cache if zone_grid_cache is not None else {}
        if key not in cache:
            cache[key] = _prepare_zone_grid(zones, reference)
        projected, zone_indices = cache[key]
    component_arrays = {
        "blue_green_ratio": blue_green_ratio(arrays["blue"], arrays["green"]),
        "ndti": ndti(arrays["red"], arrays["green"]),
        "nir_reflectance": arrays["nir"],
    }
    texture = texture_magnitude(arrays["green"])
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
            "analysis_period": str(getattr(scene, "analysis_period", "historical_baseline")),
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
        indices = zone_indices[str(zone.zone_id)]
        reason = validity_reason_at(
            masks, indices, settings.masks.minimum_valid_water_pixels_per_zone
        )
        shares = masks.shares_at(indices)
        valid_selector = masks.valid_water.ravel()[indices]
        valid_indices = indices[valid_selector]
        valid_count = len(valid_indices)
        zone_count = len(indices)
        share = valid_count / zone_count if zone_count else 0.0
        cloud_excluded_share = _share_at(masks.cloud | masks.cirrus, indices)
        shadow_excluded_share = _share_at(masks.shadow | masks.dark_shadow, indices)
        invalid_reasons: list[str] = []
        if reason != "valid":
            invalid_reasons.append(reason)
        if cloud_excluded_share > settings.masks.maximum_cloud_excluded_share:
            invalid_reasons.append("cloud_contaminated")
        if shadow_excluded_share > settings.masks.maximum_shadow_excluded_share:
            invalid_reasons.append("shadow_contaminated")
        if shares["glint_risk_share"] > settings.masks.maximum_glint_excluded_share:
            invalid_reasons.append("glint_contaminated")
        if shares["land_share"] > settings.masks.maximum_land_excluded_share:
            invalid_reasons.append("land_contaminated")
        if shares["whitewater_share"] > settings.masks.maximum_whitewater_excluded_share:
            invalid_reasons.append("whitewater_contaminated")
        burdens_ok = not invalid_reasons
        valid = (
            reason == "valid"
            and share >= settings.masks.minimum_valid_zone_pixel_share
            and burdens_ok
        )
        if reason == "valid" and share < settings.masks.minimum_valid_zone_pixel_share:
            reason = "insufficient_valid_pixel_share"
            invalid_reasons.append(reason)
        elif reason == "valid" and not burdens_ok:
            reason = invalid_reasons[0]
        base.update(
            valid=valid,
            invalid_reason="valid" if valid else reason,
            observation_status="valid" if valid else "invalid",
            observation_invalid_reasons="" if valid else ";".join(dict.fromkeys(invalid_reasons)),
            valid_pixel_count=valid_count,
            zone_pixel_count=zone_count,
            water_pixel_count=int(masks.spectral_water.ravel()[indices].sum()),
            valid_pixel_share=share,
            total_excluded_share=1.0 - share,
            water_mask_method="vector-land exclusion plus green/NIR/SWIR spectral validation v1",
            water_mask_valid_share=shares["spectral_water_share"],
            land_mixed_pixel_share=shares["land_share"],
            cloud_excluded_share=cloud_excluded_share,
            cloud_excluded_pixel_share=shares["cloud_share"],
            cloud_shadow_excluded_pixel_share=shares["shadow_share"],
            cirrus_excluded_pixel_share=shares["cirrus_share"],
            invalid_excluded_pixel_share=shares["invalid_input_share"],
            shadow_excluded_share=shadow_excluded_share,
            dark_shadow_excluded_pixel_share=shares["dark_shadow_share"],
            dark_shadow_risk=shares["dark_shadow_share"] > 0.1,
            glint_excluded_share=shares["glint_risk_share"],
            scene_glint_risk=shares["glint_risk_share"] > 0.1,
            glint_method="NIR/SWIR risk exclusion v1",
            land_excluded_share=shares["land_share"],
            land_excluded_pixel_share=shares["land_share"],
            whitewater_excluded_share=shares["whitewater_share"],
            whitewater_excluded_pixel_share=shares["whitewater_share"],
            whitewater_risk=shares["whitewater_share"] > 0.1,
            **{
                name: _median_at(values, valid_indices) for name, values in component_arrays.items()
            },
            apparent_texture_strength=_median_at(texture, valid_indices),
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
    zone_grid_cache: dict[GridKey, ZoneGrid] = {}
    for _, scene in (
        scenes.loc[scenes.selected].sort_values(["acquisition_datetime_utc", "scene_id"]).iterrows()
    ):
        records.extend(
            scene_observations(
                scene,
                zones,
                cache_directory,
                settings,
                zone_grid_cache=zone_grid_cache,
            )
        )
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    numeric_defaults = (
        "blue_green_ratio",
        "ndti",
        "nir_reflectance",
        "apparent_texture_strength",
        "valid_pixel_count",
        "zone_pixel_count",
        "water_pixel_count",
        "valid_pixel_share",
        "total_excluded_share",
        "water_mask_valid_share",
        "land_mixed_pixel_share",
        "cloud_excluded_share",
        "cloud_excluded_pixel_share",
        "cloud_shadow_excluded_pixel_share",
        "cirrus_excluded_pixel_share",
        "invalid_excluded_pixel_share",
        "shadow_excluded_share",
        "dark_shadow_excluded_pixel_share",
        "glint_excluded_share",
        "land_excluded_share",
        "land_excluded_pixel_share",
        "whitewater_excluded_share",
        "whitewater_excluded_pixel_share",
    )
    for field in numeric_defaults:
        if field not in frame:
            frame[field] = np.nan
    for field in ("dark_shadow_risk", "scene_glint_risk", "whitewater_risk"):
        if field not in frame:
            frame[field] = False
    for field, default in (
        ("water_mask_method", "not_evaluated"),
        ("glint_method", "not_evaluated"),
    ):
        if field not in frame:
            frame[field] = default
    if "observation_status" not in frame:
        frame["observation_status"] = np.where(frame.valid.astype(bool), "valid", "invalid")
    if "observation_invalid_reasons" not in frame:
        frame["observation_invalid_reasons"] = np.where(
            frame.valid.astype(bool), "", frame.invalid_reason.astype(str)
        )
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
    ranked.loc[limited, "observation_status"] = "invalid"
    ranked.loc[limited, "observation_invalid_reasons"] = "insufficient_components"
    aliases = {
        "zone_class": "zone_type",
        "blue_green_ratio_p50": "blue_green_ratio",
        "ndti_p50": "ndti",
        "nir_reflectance_p50": "nir_reflectance",
        "visible_texture_score": "apparent_texture_strength",
        "blue_green_clarity_percentile": "blue_green_ratio_clarity_percentile",
        "inverse_ndti_percentile": "ndti_clarity_percentile",
        "inverse_nir_percentile": "nir_reflectance_clarity_percentile",
        "clarity_proxy_percentile": "clarity_percentile",
        "clarity_component_count": "valid_clarity_component_count",
        "apparent_bottom_texture_valid": "apparent_bottom_texture_candidate",
    }
    for alias, source in aliases.items():
        ranked[alias] = ranked[source]
    return ranked.sort_values(["scene_id", "segment_id", "zone_type"]).reset_index(drop=True)


def build_scene_features(catalogue: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    """Create the compact one-row-per-scene processing and quality audit table."""
    selected_fields = [
        field
        for field in (
            "scene_id",
            "acquisition_datetime_utc",
            "year",
            "month",
            "tile_id",
            "processing_baseline",
            "catalogue_cloud_cover_percent",
            "aoi_coverage_share",
            "selected",
            "selection_reason",
            "required_assets_available",
            "catalogue_url_reference",
            "analysis_period",
            "partial_period_label",
        )
        if field in catalogue
    ]
    scenes = catalogue[selected_fields].copy()
    if observations.empty:
        scenes["scene_valid_segment_count"] = 0
        scenes["scene_valid_observation_count"] = 0
        scenes["scene_glint_risk"] = False
    else:
        working = observations.copy()
        if "scene_glint_risk" not in working:
            working["scene_glint_risk"] = False
        grouped = working.groupby("scene_id", sort=True)
        audit = grouped.agg(
            scene_valid_observation_count=("valid", "sum"),
            scene_glint_risk=("scene_glint_risk", "max"),
        ).reset_index()
        valid_segments = (
            working.loc[working.valid.astype(bool)]
            .groupby("scene_id")
            .segment_id.nunique()
            .rename("scene_valid_segment_count")
            .reset_index()
        )
        audit = audit.merge(valid_segments, on="scene_id", how="left")
        scenes = scenes.merge(audit, on="scene_id", how="left", validate="one_to_one")
        scenes["scene_valid_segment_count"] = scenes.scene_valid_segment_count.fillna(0).astype(int)
        scenes["scene_valid_observation_count"] = scenes.scene_valid_observation_count.fillna(
            0
        ).astype(int)
        scenes["scene_glint_risk"] = scenes.scene_glint_risk.fillna(False).astype(bool)
    scenes["scene_quality_flag"] = np.select(
        [
            ~scenes.selected.astype(bool),
            scenes.scene_valid_observation_count.eq(0),
            scenes.scene_glint_risk,
        ],
        ["not_selected", "insufficient", "glint_limited"],
        default="usable",
    )
    scenes["source_item_reference"] = scenes.get("catalogue_url_reference", "")
    order = [field for field in ("acquisition_datetime_utc", "scene_id") if field in scenes]
    return scenes.sort_values(order).reset_index(drop=True)
