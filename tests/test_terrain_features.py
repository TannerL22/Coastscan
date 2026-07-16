from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from conftest import write_dem

from coastscan.io.rasters import local_roughness, slope_degrees
from coastscan.terrain.features import calculate_terrain_features


def test_linear_ramp_has_45_degree_slope() -> None:
    y, x = np.mgrid[0:20, 0:20]
    elevation = x.astype(float)
    slope = slope_degrees(elevation, 1, 1)
    assert np.nanmedian(slope) == pytest.approx(45)


def test_nodata_stays_missing_in_slope_and_roughness() -> None:
    values = np.arange(100, dtype=float).reshape(10, 10)
    values[5, 5] = np.nan
    assert np.isnan(slope_degrees(values, 1, 1)[5, 5])
    assert np.isnan(local_roughness(values, 3)[5, 5])


def test_flat_roughness_is_zero() -> None:
    values = np.ones((10, 10), dtype=float)
    assert np.nanmax(local_roughness(values, 5)) == pytest.approx(0)


def test_synthetic_nodata_raster_fixture(tmp_path: Path) -> None:
    path = write_dem(tmp_path / "nodata.tif", nodata_zone=True)
    with rasterio.open(path) as dataset:
        assert dataset.read(1, masked=True).mask.any()


def test_steep_and_flat_raster_fixture(tmp_path: Path) -> None:
    path = write_dem(tmp_path / "zones.tif", steep_flat=True)
    with rasterio.open(path) as dataset:
        values = dataset.read(1)
        assert np.std(values[:, 10]) == 0
        assert np.std(values[:, -10]) > 0


def feature_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    segments = pd.DataFrame([{"segment_id": "s1", "orientation_status": "resolved"}])
    transects = pd.DataFrame(
        [
            {"segment_id": "s1", "transect_id": "t1", "direction": "inland"},
            {"segment_id": "s1", "transect_id": "t2", "direction": "inland"},
        ]
    )
    samples = pd.DataFrame(
        [
            {
                "segment_id": "s1",
                "transect_id": transect,
                "sample_distance_m": distance,
                "elevation_m": distance,
                "slope_deg": 45.0,
                "roughness": 2.0,
            }
            for transect in ("t1", "t2")
            for distance in (0.0, 25.0, 50.0)
        ]
    )
    return segments, transects, samples


def test_relief_and_steep_features_have_known_values() -> None:
    segments, transects, samples = feature_inputs()
    result = calculate_terrain_features(segments, transects, samples, [25, 50], 25, 35, 0.7)
    assert result.land_relief_25m_p50_m.iloc[0] == pytest.approx(25)
    assert result.land_relief_50m_p90_m.iloc[0] == pytest.approx(50)
    assert result.steep_sample_share.iloc[0] == pytest.approx(1)
    assert result.distance_to_first_steep_sample_p50_m.iloc[0] == pytest.approx(0)


def test_missing_samples_reduce_completeness_instead_of_becoming_zero() -> None:
    segments, transects, samples = feature_inputs()
    samples.loc[[1, 2], ["elevation_m", "slope_deg"]] = np.nan
    result = calculate_terrain_features(segments, transects, samples, [25, 50], 25, 35, 0.8)
    assert result.terrain_valid_sample_share.iloc[0] == pytest.approx(4 / 6)
    assert result.terrain_quality_flag.iloc[0] == "partial"
    assert not np.isnan(result.land_relief_25m_p50_m.iloc[0])
