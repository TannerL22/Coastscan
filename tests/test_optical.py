"""Deterministic Phase 3 optical contracts without external imagery."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, MultiLineString, box, mapping

from coastscan.catalog.manifests import sha256_file
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
from coastscan.optical.cache import (
    CLIP_ROLES,
    acquisition_manifest_path,
    cached_outputs_are_valid,
    clip_path,
    validate_acquisition_cache,
)
from coastscan.optical.catalogue import _rows
from coastscan.optical.indices import blue_green_ratio, ndti, regional_percentiles
from coastscan.optical.masks import build_masks, validity_reason
from coastscan.optical.observations import build_scene_features, extract_observations
from coastscan.optical.qa import (
    generate_optical_mask_qa,
    generate_optical_qa_figures,
    generate_optical_time_series_qa,
    optical_qa_summary,
)
from coastscan.optical.radiometry import Radiometry, reflectance, resampling_for_asset
from coastscan.optical.texture import apparent_texture_persistence
from coastscan.optical.zones import generate_optical_zones
from coastscan.pipeline.build_clarity import PROTECTED_FILES, build_clarity

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
    green[0, 0], nir[0, 0], swir[0, 0] = 0.10, 0.07, 0.03
    scl[0, 1] = 3
    scl[0, 2] = 5
    green[0, 3], nir[0, 3], swir[0, 3] = 0.10, 0.08, 0.03
    blue[1, 0], green[1, 0], red[1, 0], nir[1, 0] = 0.15, 0.16, 0.15, 0.06
    blue[1, 1], green[1, 1], red[1, 1] = 0.01, 0.01, 0.01
    nir[1, 1], swir[1, 1] = 0.001, 0.001
    masks = build_masks(blue, green, red, nir, swir, scl)
    assert masks.cloud[0, 0]
    assert not masks.glint_risk[0, 0]
    assert masks.shadow[0, 1]
    assert masks.land[0, 2]
    assert masks.glint_risk[0, 3]
    assert masks.whitewater[1, 0]
    assert masks.dark_shadow[1, 1]
    assert masks.valid_water[1, 2]
    reason = validity_reason(masks, np.ones(shape, dtype=bool), 3)
    assert reason.startswith("insufficient_valid_pixels")
    assert reason != "insufficient_valid_pixels:spectral_water"
    shares = masks.shares_at(np.arange(np.prod(shape)))
    assert all(0.0 <= value <= 1.0 for value in shares.values())


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
    assert {
        "zone_class",
        "inner_distance_m",
        "outer_distance_m",
        "zone_area_m2",
        "zone_geometry_status",
        "geometry_warnings",
    }.issubset(zones.columns)


def test_zone_swaths_handle_multiline_headland_and_do_not_wrap_across_bay() -> None:
    segment = MultiLineString([LineString([(0, 0), (40, 0)]), LineString([(60, 0), (100, 0)])])
    segments = gpd.GeoDataFrame(
        {
            "segment_id": ["headland"],
            "orientation_status": ["resolved"],
            "seaward_bearing_deg": [180.0],
        },
        geometry=[segment],
        crs="EPSG:3857",
    )
    config, _ = load_region_config("mallorca_northwest_pilot", ROOT)
    assert config.optical is not None
    headland = box(42, -800, 58, 20)
    unrelated_bay = box(140, -800, 300, 20)
    land = headland.union(unrelated_bay).union(box(-100, 0, 400, 200))
    first = generate_optical_zones(segments, land, config.optical.zones)
    second = generate_optical_zones(segments, land, config.optical.zones)
    assert first.zone_id.tolist() == second.zone_id.tolist()
    assert first.geometry.to_wkb().tolist() == second.geometry.to_wkb().tolist()
    assert first.geometry.notna().all()
    for geometry in first.geometry:
        assert geometry.intersection(land).area == pytest.approx(0.0)
        assert geometry.bounds[0] >= -13
        assert geometry.bounds[2] <= 113
        assert geometry.bounds[3] <= 0


def test_catalogue_prefers_latest_processing_baseline_and_rejects_missing_assets() -> None:
    config, _ = load_region_config("mallorca_northwest_pilot", ROOT)
    aoi = gpd.GeoDataFrame(geometry=[box(2.2, 39.7, 2.9, 40.1)], crs="EPSG:4326")
    keys = ["B02_10m", "B03_10m", "B04_10m", "B08_10m", "B11_20m", "SCL_20m"]

    def item(
        identifier: str,
        baseline: str,
        *,
        missing: bool = False,
        timestamp: str = "2024-07-01T10:00:00Z",
    ) -> dict[str, object]:
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
                "datetime": timestamp,
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

    partial_item = item("partial", "05.11", timestamp="2026-07-01T10:00:00Z")
    disabled = _rows([partial_item], config, aoi, "2026-07-02T00:00:00Z")
    assert disabled.selection_reason.item() == "partial_current_year_disabled"
    assert disabled.analysis_period.item() == "partial_current_year"
    assert config.optical is not None
    enabled_config = config.model_copy(
        update={"optical": config.optical.model_copy(update={"include_partial_current_year": True})}
    )
    enabled = _rows([partial_item], enabled_config, aoi, "2026-07-02T00:00:00Z")
    assert enabled.selected.item()
    assert enabled.partial_period_label.item() == "incomplete"


def test_authentication_is_runtime_only_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ACCESS_KEY_ENV, raising=False)
    monkeypatch.delenv(SECRET_KEY_ENV, raising=False)
    assert authentication_status()["ready"] is False
    with pytest.raises(AcquisitionError, match="generated CDSE S3 credentials"):
        require_s3_credentials()
    calls: list[dict[str, object]] = []

    def fake_session(**kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr("coastscan.optical.authentication.AWSSession", fake_session)
    credentials = CopernicusS3Credentials("visible-key", "visible-secret")
    assert "visible" not in repr(credentials)
    assert credentials.rasterio_session("https://example.invalid/") is not None
    assert calls == [
        {
            "aws_access_key_id": "visible-key",
            "aws_secret_access_key": "visible-secret",
            "region_name": "default",
            "endpoint_url": "https://example.invalid",
        }
    ]
    assert credentials.rasterio_options() == {
        "AWS_HTTPS": "YES",
        "AWS_VIRTUAL_HOSTING": "FALSE",
    }


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


def test_persistence_uses_month_year_medians_not_raw_scene_share() -> None:
    observations = pd.DataFrame(
        {
            "segment_id": ["a"] * 5,
            "zone_type": ["nearshore"] * 5,
            "scene_id": [f"s{index}" for index in range(5)],
            "year": [2024] * 5,
            "month": [6, 6, 6, 6, 7],
            "clarity_percentile": [90, 90, 90, 90, 10],
            "valid": True,
        }
    )
    seasonal = aggregate_periods(
        observations,
        {"summer_jja": [6, 7, 8]},
        clear_threshold=75,
        turbid_threshold=25,
        minimum_scenes=1,
        minimum_months=1,
    )
    row = seasonal.iloc[0]
    assert row.clear_water_observation_share == pytest.approx(0.8)
    assert row.clarity_persistence == pytest.approx(0.5)
    assert row.clarity_percentile_p10 == pytest.approx(42.0)
    assert row.clarity_variability_mad == pytest.approx(0.0)


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


def test_acquisition_cache_verifies_every_selected_asset_and_output(
    tmp_path: Path,
) -> None:
    selected = pd.DataFrame({"scene_id": ["scene-a", "scene-b"]})
    files = []
    for scene_id in selected.scene_id:
        for role in CLIP_ROLES:
            path = clip_path(tmp_path, "demo", scene_id, role)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{scene_id}:{role}".encode())
            files.append(
                {
                    "scene_id": scene_id,
                    "asset_role": role,
                    "path": path.relative_to(tmp_path).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    manifest_path = acquisition_manifest_path(tmp_path, "demo")
    manifest_path.write_text(
        json.dumps(
            {
                "region_id": "demo",
                "catalogue_checksum": "catalogue-sha",
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    validated = validate_acquisition_cache(
        tmp_path, "demo", selected, catalogue_checksum="catalogue-sha"
    )
    assert validated.file_count == 2 * len(CLIP_ROLES)
    output = tmp_path / "data/processed/demo/segment_features_phase3.parquet"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"output")
    output_relative = output.relative_to(tmp_path).as_posix()
    build_manifest = {
        "output_files": [output_relative],
        "output_checksums": {output_relative: sha256_file(output)},
    }
    assert cached_outputs_are_valid(build_manifest, tmp_path)
    output.write_bytes(b"changed")
    assert not cached_outputs_are_valid(build_manifest, tmp_path)
    clip_path(tmp_path, "demo", "scene-a", "blue").write_bytes(b"tampered")
    with pytest.raises(AcquisitionError, match="changed"):
        validate_acquisition_cache(tmp_path, "demo", selected, catalogue_checksum="catalogue-sha")

    partial = json.loads(manifest_path.read_text(encoding="utf-8"))
    partial["complete"] = False
    manifest_path.write_text(json.dumps(partial), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="incomplete"):
        validate_acquisition_cache(tmp_path, "demo", selected, catalogue_checksum="catalogue-sha")


def test_synthetic_scene_extraction_aligns_10m_and_20m_assets(tmp_path: Path) -> None:
    cache = tmp_path / "clips"
    scene_dir = cache / "synthetic-scene"
    scene_dir.mkdir(parents=True)
    transform_10m = from_origin(0, 200, 10, 10)
    transform_20m = from_origin(0, 200, 20, 20)
    columns = np.repeat(np.arange(5), 4)
    blue = np.tile(900 + columns * 120, (20, 1)).astype("uint16")
    green = np.full((20, 20), 800, dtype="uint16")
    red = np.tile(600 - columns * 40, (20, 1)).astype("uint16")
    nir = np.full((20, 20), 100, dtype="uint16")
    swir = np.full((10, 10), 50, dtype="uint16")
    scl = np.full((10, 10), 6, dtype="uint8")

    def write(path: Path, values: np.ndarray, transform: object) -> None:
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=values.shape[0],
            width=values.shape[1],
            count=1,
            dtype=str(values.dtype),
            crs="EPSG:3857",
            transform=transform,
            nodata=0,
        ) as dataset:
            dataset.write(values, 1)

    for role, values in {
        "blue": blue,
        "green": green,
        "red": red,
        "nir": nir,
    }.items():
        write(scene_dir / f"{role}.tif", values, transform_10m)
    write(scene_dir / "swir1.tif", swir, transform_20m)
    write(scene_dir / "scl.tif", scl, transform_20m)
    metadata = {
        role: {
            "scale": 1.0 if role == "scene_classification" else 0.0001,
            "offset": 0,
            "nodata": 0,
        }
        for role in ("blue", "green", "red", "nir", "swir1", "scene_classification")
    }
    scenes = pd.DataFrame(
        {
            "scene_id": ["synthetic-scene"],
            "selected": [True],
            "acquisition_datetime_utc": ["2024-07-01T10:00:00+00:00"],
            "year": [2024],
            "month": [7],
            "analysis_period": ["historical_baseline"],
            "processing_baseline": ["05.11"],
            "asset_checksums_or_etags": [json.dumps(metadata)],
        }
    )
    zones = gpd.GeoDataFrame(
        {
            "zone_id": [f"segment-{index}:nearshore" for index in range(5)],
            "segment_id": [f"segment-{index}" for index in range(5)],
            "zone_type": ["nearshore"] * 5,
            "zone_status": ["valid"] * 5,
        },
        geometry=[box(index * 40, 0, (index + 1) * 40, 200) for index in range(5)],
        crs="EPSG:3857",
    )
    config, _ = load_region_config("mallorca_northwest_pilot", ROOT)
    assert config.optical is not None
    observations = extract_observations(scenes, zones, cache, config.optical)
    assert len(observations) == 5
    assert observations.valid.all()
    assert observations.valid_clarity_component_count.eq(3).all()
    assert observations.clarity_percentile.notna().all()
    assert observations.sort_values("segment_id").clarity_percentile.is_monotonic_increasing
    assert observations.total_excluded_share.between(0, 1).all()
    assert observations.shadow_excluded_share.between(0, 1).all()
    assert {
        "zone_class",
        "water_pixel_count",
        "cloud_shadow_excluded_pixel_share",
        "dark_shadow_excluded_pixel_share",
        "blue_green_ratio_p50",
        "clarity_proxy_percentile",
        "observation_status",
        "observation_invalid_reasons",
    }.issubset(observations.columns)
    catalogue = scenes.assign(
        tile_id="31TDE",
        catalogue_cloud_cover_percent=5.0,
        aoi_coverage_share=1.0,
        selection_reason="candidate",
        required_assets_available=True,
        catalogue_url_reference="https://example.invalid/item",
    )
    scene_features = build_scene_features(catalogue, observations)
    assert scene_features.scene_valid_segment_count.item() == 5
    assert scene_features.scene_quality_flag.item() == "usable"
    mask_outputs = generate_optical_mask_qa(catalogue, cache, tmp_path / "mask-qa")
    assert len(mask_outputs) == 1
    assert mask_outputs[0].is_file() and mask_outputs[0].stat().st_size > 0


def test_qa_summary_and_figures_cover_catalogue_masks_zones_and_periods(
    tmp_path: Path,
) -> None:
    catalogue = pd.DataFrame(
        {
            "scene_id": ["s1", "s2"],
            "selected": [True, False],
            "selection_reason": ["candidate", "catalogue_cloud_cover_above_limit"],
            "year": [2024, 2024],
            "month": [7, 7],
            "catalogue_cloud_cover_percent": [5.0, 95.0],
            "processing_baseline": ["05.11", "05.11"],
            "tile_id": ["31TDE", "31TDE"],
        }
    )
    segments = gpd.GeoDataFrame(
        {"segment_id": ["a", "b"]},
        geometry=[LineString([(0, 0), (10, 0)]), LineString([(20, 0), (30, 0)])],
        crs="EPSG:3857",
    )
    zones = gpd.GeoDataFrame(
        {
            "segment_id": ["a", "b"],
            "zone_type": ["nearshore", "nearshore"],
            "zone_status": ["valid", "ambiguous_orientation"],
        },
        geometry=[box(0, -10, 10, 0), None],
        crs="EPSG:3857",
    )
    observations = pd.DataFrame(
        {
            "segment_id": ["a", "b"],
            "scene_id": ["s1", "s1"],
            "month": [7, 7],
            "valid": [True, False],
            "invalid_reason": ["valid", "ambiguous_orientation"],
            "valid_pixel_share": [0.9, 0.0],
            "cloud_excluded_share": [0.05, 0.0],
            "shadow_excluded_share": [0.02, 0.0],
            "glint_excluded_share": [0.01, 0.0],
            "land_excluded_share": [0.02, 0.0],
            "whitewater_excluded_share": [0.0, 0.0],
            "clarity_percentile": [80.0, np.nan],
        }
    )
    seasonal = pd.DataFrame(
        {
            "segment_id": ["a", "b"],
            "zone_type": ["nearshore", "nearshore"],
            "period_id": ["july", "july"],
            "clarity_percentile_p50": [80.0, np.nan],
            "clear_water_observation_share": [1.0, np.nan],
            "clarity_persistence": [1.0, np.nan],
            "clarity_variability_iqr": [0.0, np.nan],
            "bottom_texture_status": ["insufficient", "insufficient"],
        }
    )
    clarity = pd.DataFrame(
        {
            "segment_id": ["a", "b"],
            "valid_scene_count": [1, 0],
            "clarity_percentile_p50": [80.0, np.nan],
            "clear_water_observation_share": [1.0, np.nan],
            "clarity_persistence": [1.0, np.nan],
            "clarity_variability_iqr": [0.0, np.nan],
            "best_month": ["july", None],
            "clarity_data_confidence": ["low", "insufficient"],
            "clarity_quality_flag": ["limited", "insufficient"],
        }
    )
    summary = optical_qa_summary(
        catalogue, zones, observations, seasonal, clarity, timings={"total": 1.0}
    )
    assert summary["selected_scenes"] == 1
    assert summary["invalid_reasons"] == {"ambiguous_orientation": 1}
    outputs = generate_optical_qa_figures(
        catalogue, segments, zones, seasonal, clarity, tmp_path / "qa"
    )
    assert outputs
    assert all(path.is_file() and path.stat().st_size > 0 for path in outputs)
    time_series = generate_optical_time_series_qa(observations, clarity, tmp_path / "qa")
    assert time_series is not None and time_series.stat().st_size > 0


def test_synthetic_phase3_build_manifest_cache_and_protected_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    region_id = "mallorca_northwest_pilot"
    config_directory = tmp_path / "config/regions"
    config_directory.mkdir(parents=True)
    config_path = config_directory / f"{region_id}.yml"
    config_path.write_text(
        (ROOT / "config/regions/mallorca_northwest_pilot.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    processed = tmp_path / "data/processed" / region_id
    processed.mkdir(parents=True)
    segments = gpd.GeoDataFrame(
        {
            "segment_id": ["a", "b"],
            "region_id": [region_id, region_id],
            "orientation_status": ["resolved", "ambiguous"],
            "seaward_bearing_deg": [180.0, np.nan],
        },
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(200, 0), (300, 0)])],
        crs="EPSG:25831",
    )
    phase2 = segments.copy()
    phase2["land_relief_100m_p90_m"] = [20.0, 5.0]
    phase2["bathymetry_screening_class"] = ["background_only", "insufficient"]
    segments.to_parquet(processed / "coast_segments.parquet", index=False)
    segments.to_parquet(processed / "segment_features.parquet", index=False)
    pd.DataFrame({"segment_id": ["a"]}).to_parquet(
        processed / "bathymetry_transects.parquet", index=False
    )
    pd.DataFrame({"segment_id": ["a", "b"]}).to_parquet(
        processed / "bathymetry_features.parquet", index=False
    )
    phase2.to_parquet(processed / "segment_features_phase2.parquet", index=False)
    before = {name: sha256_file(processed / name) for name in PROTECTED_FILES}

    scene_ids = [f"scene-{month}" for month in range(5, 10)]
    catalogue = pd.DataFrame(
        {
            "scene_id": scene_ids,
            "selected": True,
            "selection_reason": "candidate",
            "year": 2024,
            "month": list(range(5, 10)),
            "analysis_period": "historical_baseline",
            "catalogue_cloud_cover_percent": [5.0, 10.0, 15.0, 20.0, 25.0],
            "processing_baseline": "05.11",
            "tile_id": "31TDE",
        }
    )
    catalogue_path = tmp_path / "data_catalog/optical" / f"{region_id}_scenes.parquet"
    catalogue_path.parent.mkdir(parents=True)
    catalogue.to_parquet(catalogue_path, index=False)
    catalogue_checksum = sha256_file(catalogue_path)
    acquisition_files = []
    for scene_id in scene_ids:
        for role in CLIP_ROLES:
            path = clip_path(tmp_path, region_id, scene_id, role)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{scene_id}:{role}".encode())
            acquisition_files.append(
                {
                    "scene_id": scene_id,
                    "asset_role": role,
                    "path": path.relative_to(tmp_path).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    acquisition_manifest_path(tmp_path, region_id).write_text(
        json.dumps(
            {
                "region_id": region_id,
                "catalogue_checksum": catalogue_checksum,
                "files": acquisition_files,
            }
        ),
        encoding="utf-8",
    )
    zones = gpd.GeoDataFrame(
        {
            "zone_id": ["a:nearshore", "b:nearshore"],
            "segment_id": ["a", "b"],
            "zone_type": ["nearshore", "nearshore"],
            "zone_status": ["valid", "ambiguous_orientation"],
        },
        geometry=[box(0, -100, 100, 0), None],
        crs=segments.crs,
    )
    observation_rows = []
    for scene_id, month in zip(scene_ids, range(5, 10), strict=True):
        for segment_id, clarity_value, valid in (("a", 80.0, True), ("b", np.nan, False)):
            observation_rows.append(
                {
                    "segment_id": segment_id,
                    "zone_type": "nearshore",
                    "scene_id": scene_id,
                    "year": 2024,
                    "month": month,
                    "analysis_period": "historical_baseline",
                    "clarity_percentile": clarity_value,
                    "valid": valid,
                    "invalid_reason": "valid" if valid else "ambiguous_orientation",
                    "valid_pixel_share": 0.9 if valid else 0.0,
                    "cloud_excluded_share": 0.05,
                    "shadow_excluded_share": 0.02,
                    "glint_excluded_share": 0.01,
                    "land_excluded_share": 0.01,
                    "whitewater_excluded_share": 0.0,
                    "apparent_bottom_texture_candidate": False,
                }
            )
    observations = pd.DataFrame(observation_rows)
    metadata = {
        "catalogue_checksum": catalogue_checksum,
        "query_fingerprint": "synthetic",
    }
    monkeypatch.setattr(
        "coastscan.pipeline.build_clarity.discover_scene_catalogue",
        lambda *args, **kwargs: (catalogue.copy(), metadata.copy()),
    )
    monkeypatch.setattr(
        "coastscan.pipeline.build_clarity.zones_for_region",
        lambda *args, **kwargs: zones.copy(),
    )
    monkeypatch.setattr(
        "coastscan.pipeline.build_clarity.extract_observations",
        lambda *args, **kwargs: observations.copy(),
    )
    monkeypatch.setattr(
        "coastscan.pipeline.build_clarity.generate_optical_mask_qa",
        lambda *args, **kwargs: [],
    )
    first = build_clarity(region_id, root=tmp_path, force=True, write_observations=True)
    assert first["status"] == "success"
    assert first["feature_counts"]["clarity_feature_rows"] == 2
    assert first["acquired_clip_count"] == len(scene_ids) * len(CLIP_ROLES)
    assert (processed / "segment_features_phase3.parquet").is_file()
    assert (processed / "clarity_scenes.parquet").is_file()
    assert (processed / "clarity_zones.parquet").is_file()
    assert (tmp_path / "outputs/qa" / region_id / "optical/optical_qa_summary.json").is_file()
    timestamped = [
        path
        for path in (tmp_path / "outputs/manifests" / region_id).glob("*_clarity.json")
        if path.name != "latest_clarity.json"
    ]
    assert len(timestamped) == 1
    assert {name: sha256_file(processed / name) for name in PROTECTED_FILES} == before
    second = build_clarity(region_id, root=tmp_path, write_observations=True)
    assert second["cache_used"] is True
    clip_path(tmp_path, region_id, scene_ids[0], "blue").write_bytes(b"tampered")
    with pytest.raises(AcquisitionError, match="changed"):
        build_clarity(region_id, root=tmp_path)
