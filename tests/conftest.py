"""Deterministic synthetic geometry and raster fixtures."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, Polygon, box


@pytest.fixture
def rectangular_island() -> Polygon:
    return box(0, 0, 1000, 500)


@pytest.fixture
def curved_peninsula() -> Polygon:
    return box(0, 0, 600, 300).union(box(250, 250, 350, 600).buffer(75, resolution=8))


@pytest.fixture
def narrow_headland() -> Polygon:
    return box(-20, 0, 20, 200).union(box(-100, 180, 100, 260))


@pytest.fixture
def multipart_islands() -> MultiPolygon:
    return MultiPolygon([box(0, 0, 500, 500), box(700, 100, 750, 150)])


@pytest.fixture
def lake_polygon() -> Polygon:
    return Polygon(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)],
        holes=[[(400, 400), (600, 400), (600, 600), (400, 600), (400, 400)]],
    )


def write_dem(
    path: Path,
    *,
    nodata_zone: bool = False,
    steep_flat: bool = False,
) -> Path:
    pixel = 5.0
    rows, columns = 200, 300
    y = 750 - (np.arange(rows) + 0.5) * pixel
    values = np.repeat(y[:, None], columns, axis=1).astype("float32")
    if steep_flat:
        values[:, : columns // 2] = 10
    if nodata_zone:
        values[50:80, 50:80] = -9999
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=rows,
        width=columns,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(-250, 750, pixel, pixel),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
    return path


@pytest.fixture
def synthetic_project(tmp_path: Path) -> Path:
    (tmp_path / "config" / "regions").mkdir(parents=True)
    (tmp_path / "data" / "fixtures").mkdir(parents=True)
    (tmp_path / "data_catalog").mkdir()
    land_path = tmp_path / "data" / "fixtures" / "land.geojson"
    gpd.GeoDataFrame(
        {"name": ["synthetic"]}, geometry=[box(0, 0, 1000, 500)], crs="EPSG:3857"
    ).to_file(land_path, driver="GeoJSON")
    write_dem(tmp_path / "data" / "fixtures" / "dem.tif")
    (tmp_path / "data_catalog" / "sources.csv").write_text(
        "source_id,provider,dataset_name,version,source_type,coverage,native_crs,"
        "horizontal_resolution,vertical_units,licence,source_url,download_date,local_path,"
        "checksum,notes\n"
        "land_fixture,tests,Synthetic land,1,vector,Synthetic,EPSG:3857,n/a,metres,test,"
        "n/a,n/a,data/fixtures/land.geojson,,fixture\n"
        "dem_fixture,tests,Synthetic DEM,1,raster,Synthetic,EPSG:3857,5 m,metres,test,"
        "n/a,n/a,data/fixtures/dem.tif,,fixture\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "regions" / "demo.yml").write_text(
        """region_id: demo
region_name: Synthetic Demo
country: SYNTHETIC
analysis_crs: EPSG:3857
output_crs: EPSG:4326
inputs:
  land_polygon: {path: data/fixtures/land.geojson, source_id: land_fixture}
  elevation: {path: data/fixtures/dem.tif, source_id: dem_fixture}
coastline:
  target_segment_length_m: 250
  minimum_segment_length_m: 75
  simplification_tolerance_m: 0
  orientation_test_distance_m: 10
  orientation_fallback_distances_m: [20, 40]
  include_interior_shorelines: false
transects: {spacing_m: 25, inland_length_m: 100, offshore_length_m: 200}
terrain:
  relief_distances_m: [25, 50, 100]
  sample_spacing_m: 5
  steep_slope_threshold_degrees: 35
  roughness_window_m: 15
  minimum_valid_sample_share: 0.7
  write_samples: true
quality:
  maximum_ambiguous_orientation_share: 0.02
  maximum_missing_terrain_share: 0.05
  random_qa_sample_size: 10
""",
        encoding="utf-8",
    )
    return tmp_path
