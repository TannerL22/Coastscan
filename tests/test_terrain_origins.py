from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString

from coastscan.terrain.features import calculate_terrain_features
from coastscan.terrain.sampling import sample_terrain


def write_raster(path: Path, values: np.ndarray) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0, 1, 1, 1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values.astype("float32"), 1)
    return path


def sample_fixture(tmp_path: Path, elevations: list[float], search: float):
    dem_values = np.asarray([elevations], dtype=float)
    dem_values[~np.isfinite(dem_values)] = -9999
    dem = write_raster(tmp_path / "dem.tif", dem_values)
    slope = write_raster(tmp_path / "slope.tif", np.ones_like(dem_values) * 40)
    roughness = write_raster(tmp_path / "rough.tif", np.ones_like(dem_values) * 2)
    transects = gpd.GeoDataFrame(
        {
            "transect_id": ["t1"],
            "segment_id": ["s1"],
            "direction": ["inland"],
        },
        geometry=[LineString([(0.5, 0.5), (5.5, 0.5)])],
        crs="EPSG:3857",
    )
    return transects, sample_terrain(transects, dem, slope, roughness, 1, search)


def test_exact_valid_origin_is_preserved(tmp_path: Path) -> None:
    _, samples = sample_fixture(tmp_path, [7, 8, 9, 10, 11, 12], 3)
    assert samples.terrain_origin_method.unique().tolist() == ["exact"]
    assert samples.terrain_origin_shift_m.iloc[0] == pytest.approx(0)
    assert samples.terrain_origin_elevation_m.iloc[0] == pytest.approx(7)


def test_nodata_origin_shifts_to_nearest_valid_inland_sample(tmp_path: Path) -> None:
    _, samples = sample_fixture(tmp_path, [np.nan, np.nan, 9, 10, 11, 12], 3)
    assert samples.terrain_origin_method.unique().tolist() == ["shifted_inland"]
    assert samples.terrain_origin_shift_m.iloc[0] == pytest.approx(2)
    assert samples.terrain_origin_elevation_m.iloc[0] == pytest.approx(9)
    assert samples.elevation_m.iloc[0] != 0


def test_no_valid_inland_origin_stays_missing(tmp_path: Path) -> None:
    _, samples = sample_fixture(tmp_path, [np.nan] * 6, 3)
    assert samples.terrain_origin_method.unique().tolist() == ["unavailable"]
    assert np.isnan(samples.terrain_origin_elevation_m.iloc[0])
    assert samples.elevation_m.isna().all()


def test_shift_is_recorded_in_segment_features(tmp_path: Path) -> None:
    transects, samples = sample_fixture(tmp_path, [np.nan, np.nan, 9, 10, 11, 12], 3)
    segments = gpd.GeoDataFrame({"segment_id": ["s1"], "orientation_status": ["resolved"]})
    features = calculate_terrain_features(segments, transects, samples, [2, 5], 1, 35, 0.5)
    assert features.terrain_origin_method.iloc[0] == "shifted_inland"
    assert features.terrain_origin_shift_m.iloc[0] == pytest.approx(2)
    assert features.land_relief_5m_p50_m.iloc[0] == pytest.approx(3)
