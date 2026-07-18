"""Deterministic Phase 3 optical contracts without external imagery."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, box, mapping

from coastscan.config import load_region_config
from coastscan.exceptions import AcquisitionError
from coastscan.optical.aggregation import aggregate_periods, best_month, headline_features
from coastscan.optical.authentication import (
    ACCESS_KEY_ENV,
    SECRET_KEY_ENV,
    CopernicusS3Credentials,
    authentication_status,
    require_s3_credentials,
)
from coastscan.optical.catalogue import _rows
from coastscan.optical.indices import blue_green_ratio, ndti, regional_percentiles
from coastscan.optical.masks import build_masks, validity_reason
from coastscan.optical.radiometry import Radiometry, reflectance, resampling_for_asset
from coastscan.optical.texture import apparent_texture_persistence
from coastscan.optical.zones import generate_optical_zones

ROOT = Path(__file__).parents[1]


def test_radiometry_scale_offset_nodata_and_resampling() -> None:
    values = np.array([[0, 1000, -9999]], dtype="int16")
    result = reflectance(values, Radiometry(0.0001, -0.1, -9999, "05.11"))
    assert result[0, 0] == pytest.approx(-0.1)
    assert result[0, 1] == pytest.approx(0.0, abs=1e-7)
    assert np.isnan(result[0, 2])
    assert resampling_for_asset(categorical=False).name == "bilinear"
    assert resampling_for_asset(categorical=True).name == "nearest"


def test_masks_preserve_cloud_shadow_land_glint_whitewater_and_reason() -> None:
    shape = (2, 4)
    blue = np.full(shape, 0.05)
    green = np.full(shape, 0.06)
    red = np.full(shape, 0.04)
    nir = np.full(shape, 0.01)
    swir = np.full(shape, 0.005)
    scl = np.full(shape, 6, dtype="uint8")
    scl[0, 0] = 9
    scl[0, 1] = 3
    scl[0, 2] = 5
    nir[0, 3], swir[0, 3] = 0.08, 0.03
    blue[1, 0], green[1, 0], red[1, 0], nir[1, 0] = 0.15, 0.16, 0.15, 0.06
    blue[1, 1], green[1, 1], red[1, 1] = 0.01, 0.01, 0.01
    nir[1, 1], swir[1, 1] = 0.001, 0.001
    masks = build_masks(blue, green, red, nir, swir, scl)
    assert masks.cloud[0, 0]
    assert masks.shadow[0, 1]
    assert masks.land[0, 2]
    assert masks.glint_risk[0, 3]
    assert masks.whitewater[1, 0]
    assert masks.dark_shadow[1, 1]
    assert masks.valid_water[1, 2]
    assert validity_reason(masks, np.ones(shape, dtype=bool), 3).startswith(
        "insufficient_valid_pixels"
    )


def test_indices_are_numerically_stable_and_directional() -> None:
    blue = np.array([0.2, 0.1, 1.0])
    green = np.array([0.1, 0.1, 0.0])
    red = np.array([0.05, 0.1, 0.0])
    assert blue_green_ratio(blue, green)[:2].tolist() == [2.0, 1.0]
    assert np.isnan(blue_green_ratio(blue, green)[2])
    assert ndti(red, green)[0] < ndti(red, green)[1]
    frame = pd.DataFrame(
        {
            "scene_id": ["s"] * 5,
            "zone_type": ["nearshore"] * 5,
            "blue_green_ratio": [1, 2, 3, 4, 5],
            "ndti": [5, 4, 3, 2, 1],
            "nir_reflectance": [5, 4, 3, 2, 1],
        }
    )
    ranked = regional_percentiles(frame, minimum_population=5)
    assert ranked.clarity_percentile.is_monotonic_increasing
    constant = frame.assign(blue_green_ratio=1, ndti=1, nir_reflectance=1)
    assert (regional_percentiles(constant, minimum_population=5).clarity_percentile == 50).all()


def test_zone_generation_is_deterministic_seaward_and_excludes_ambiguous() -> None:
    segments = gpd.GeoDataFrame(
        {
            "segment_id": ["a", "b"],
            "orientation_status": ["resolved", "ambiguous"],
            "seaward_bearing_deg": [180.0, np.nan],
        },
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(200, 0), (300, 0)])],
        crs="EPSG:3857",
    )
    config, _ = load_region_config("mallorca_northwest_pilot", ROOT)
    assert config.optical is not None
    zones = generate_optical_zones(segments, box(-100, 0, 400, 200), config.optical.zones)
    assert zones.zone_id.tolist() == [
        "a:nearshore",
        "a:coastal",
        "a:context",
        "b:nearshore",
        "b:coastal",
        "b:context",
    ]
    valid = zones[zones.segment_id == "a"]
    assert valid.geometry.notna().all()
    assert all(geometry.bounds[3] <= 0 for geometry in valid.geometry)
    assert (zones[zones.segment_id == "b"].zone_status == "ambiguous_orientation").all()


def test_catalogue_prefers_latest_processing_baseline_and_rejects_missing_assets() -> None:
    config, _ = load_region_config("mallorca_northwest_pilot", ROOT)
    aoi = gpd.GeoDataFrame(geometry=[box(2.2, 39.7, 2.9, 40.1)], crs="EPSG:4326")
    keys = ["B02_10m", "B03_10m", "B04_10m", "B08_10m", "B11_20m", "SCL_20m"]

    def item(identifier: str, baseline: str, *, missing: bool = False) -> dict[str, object]:
        assets = {
            key: {"href": f"s3://eodata/{identifier}/{key}.jp2", "file:size": 100} for key in keys
        }
        if missing:
            assets.pop("SCL_20m")
        return {
            "id": identifier,
            "collection": "sentinel-2-l2a",
            "geometry": mapping(box(2.0, 39.5, 3.0, 40.5)),
            "properties": {
                "datetime": "2024-07-01T10:00:00Z",
                "grid:code": "MGRS-31TDE",
                "processing:version": baseline,
                "eo:cloud_cover": 10,
            },
            "assets": assets,
        }

    frame = _rows(
        [item("old", "05.10"), item("new", "05.11"), item("missing", "05.12", missing=True)],
        config,
        aoi,
        "2026-01-01T00:00:00Z",
    )
    assert frame.loc[frame.scene_id == "new", "selected"].item()
    assert (
        frame.loc[frame.scene_id == "old", "selection_reason"].item()
        == "duplicate_lower_preference"
    )
    assert (
        frame.loc[frame.scene_id == "missing", "selection_reason"].item()
        == "missing_required_asset"
    )
    assert frame.loc[frame.scene_id == "new", "estimated_source_bytes"].item() == 600
    assert 0 < frame.loc[frame.scene_id == "new", "aoi_to_scene_area_share"].item() < 1


def test_authentication_is_runtime_only_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ACCESS_KEY_ENV, raising=False)
    monkeypatch.delenv(SECRET_KEY_ENV, raising=False)
    assert authentication_status()["ready"] is False
    with pytest.raises(AcquisitionError, match="generated CDSE S3 credentials"):
        require_s3_credentials()
    credentials = CopernicusS3Credentials("visible-key", "visible-secret")
    assert "visible" not in repr(credentials)
    assert (
        credentials.rasterio_options("https://example.invalid")["AWS_S3_ENDPOINT"]
        == "example.invalid"
    )


def test_monthly_seasonal_aggregation_confidence_and_headline() -> None:
    observations = pd.DataFrame(
        {
            "segment_id": ["a"] * 6,
            "zone_type": ["nearshore"] * 6,
            "scene_id": [f"s{i}" for i in range(6)],
            "year": [2022, 2022, 2023, 2023, 2024, 2024],
            "month": [6, 6, 7, 7, 8, 8],
            "clarity_percentile": [80, 90, 20, 30, 70, 75],
            "valid": [True] * 6,
            "glint_excluded_share": [0.1] * 6,
        }
    )
    periods = {"june": [6], "july": [7], "august": [8], "extended_summer_may_sep": [5, 6, 7, 8, 9]}
    seasonal = aggregate_periods(
        observations,
        periods,
        clear_threshold=75,
        turbid_threshold=25,
        minimum_scenes=3,
        minimum_months=2,
    )
    extended = seasonal[seasonal.period_id == "extended_summer_may_sep"].iloc[0]
    assert extended.valid_scene_count == 6
    assert extended.valid_year_count == 3
    assert extended.clear_water_observation_share == pytest.approx(0.5)
    assert headline_features(seasonal).segment_id.tolist() == ["a"]
    choices = best_month(seasonal)
    assert choices.best_month.item() == "june"


def test_bottom_texture_requires_repeatability() -> None:
    image = np.arange(25, dtype=float).reshape(5, 5)
    valid = np.ones((5, 5), dtype=bool)
    stable = apparent_texture_persistence(
        [image, image * 2, image + 1], [valid] * 3, minimum_scenes=3
    )
    assert stable["status"] == "repeatable"
    transient = apparent_texture_persistence(
        [image, np.flipud(image), np.rot90(image)], [valid] * 3, minimum_scenes=3
    )
    assert transient["persistence"] < 1
    assert (
        apparent_texture_persistence([image], [valid], minimum_scenes=3)["status"] == "insufficient"
    )
