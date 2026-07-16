import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box

from coastscan.pipeline.build_region import build_region, inspect_region_inputs


def write_real_like_tile(path: Path, left: float) -> None:
    pixel = 5.0
    width, height = 120, 180
    x = left + (np.arange(width) + 0.5) * pixel
    y = 450 - (np.arange(height) + 0.5) * pixel
    x_grid, y_grid = np.meshgrid(x, y)
    coast_y = 20 * np.sin(x_grid / 80)
    values = (y_grid - coast_y).astype("float32")
    values[np.abs(y_grid - coast_y) < 6] = -9999
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(left, 450, pixel, pixel),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
        dataset.update_tags(1, VERTICAL_UNITS="metres")


def create_real_like_project(root: Path) -> None:
    for directory in (
        root / "config/regions",
        root / "data/fixtures",
        root / "data_catalog",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    x_values = np.linspace(0, 1000, 101)
    land_edge = [(float(x), float(20 * math.sin(x / 80))) for x in x_values]
    main_land = Polygon([*land_edge, (1000, 400), (0, 400), land_edge[0]])
    # Keep the island small relative to the mainland coast, but wider than the
    # longest inland transect so a correctly oriented transect remains on land.
    island = Point(250, -250).buffer(80)
    gpd.GeoDataFrame(
        {"name": ["main", "small_island"]},
        geometry=[main_land, island],
        crs="EPSG:3857",
    ).to_file(root / "data/fixtures/land.geojson", driver="GeoJSON")
    high_main = LineString([(x, y - 2) for x, y in land_edge])
    high = MultiLineString([high_main, LineString(island.exterior.coords)])
    low = LineString([(x, y - 5) for x, y in land_edge])
    gpd.GeoDataFrame(
        {
            "LOCALID": ["high", "low"],
            "CATEGORIA": ["COALNE", "COALNE"],
            "PLEAMAR": [True, False],
            "BAJAMAR": [False, True],
            "CIERRACOST": [True, True],
        },
        geometry=[high, low],
        crs="EPSG:3857",
    ).to_file(root / "data/fixtures/coast.geojson", driver="GeoJSON")
    gpd.GeoDataFrame(
        {"aoi_id": ["real_like"]}, geometry=[box(-40, -350, 1040, 430)], crs="EPSG:3857"
    ).to_file(root / "data/fixtures/aoi.geojson", driver="GeoJSON")
    write_real_like_tile(root / "data/fixtures/west.tif", -100)
    write_real_like_tile(root / "data/fixtures/east.tif", 500)
    (root / "data_catalog/sources.csv").write_text(
        "source_id,provider,dataset_name,version,source_type,coverage,native_crs,"
        "horizontal_resolution,vertical_units,licence,source_url,download_date,local_path,"
        "checksum,required_attribution,notes\n"
        "coast,tests,Real-like coast,1,vector,Synthetic,EPSG:3857,n/a,,test,n/a,n/a,"
        "data/fixtures/coast.geojson,,test,fixture\n"
        "land,tests,Offset mask,1,vector,Synthetic,EPSG:3857,n/a,,test,n/a,n/a,"
        "data/fixtures/land.geojson,,test,fixture\n"
        "dem,tests,Two tile DEM,1,raster,Synthetic,EPSG:3857,5 m,metres,test,n/a,n/a,"
        "data/fixtures/*.tif,,test,fixture\n",
        encoding="utf-8",
    )
    (root / "config/regions/real_like.yml").write_text(
        """region_id: real_like
region_name: Real-like Two-tile Pilot
country: SYNTHETIC
analysis_crs: EPSG:3857
output_crs: EPSG:4326
inputs:
  coastline:
    mode: direct
    path: data/fixtures/coast.geojson
    source_id: coast
    source_id_field: LOCALID
    source_class_field: CATEGORIA
    feature_filters:
      - {field: PLEAMAR, accepted_values: [true]}
  land_polygon:
    path: data/fixtures/land.geojson
    source_id: land
    role: orientation_mask
  elevation:
    source_id: dem
    paths: [data/fixtures/west.tif, data/fixtures/east.tif]
    mosaic_mode: vrt
    vertical_units: metres
area_of_interest: {path: data/fixtures/aoi.geojson}
coastline:
  target_segment_length_m: 100
  minimum_segment_length_m: 30
  simplification_tolerance_m: 0
  orientation_test_distance_m: 15
  orientation_fallback_distances_m: [30, 60]
  orientation_vote_offsets_m: [-20, 0, 20]
  source_mismatch_tolerance_m: 1
transects: {spacing_m: 20, inland_length_m: 100, offshore_length_m: 100}
terrain:
  relief_distances_m: [25, 50, 100]
  sample_spacing_m: 5
  steep_slope_threshold_degrees: 35
  roughness_window_m: 15
  minimum_valid_sample_share: 0.6
  origin_search_max_distance_m: 20
  write_samples: true
quality:
  maximum_ambiguous_orientation_share: 0.1
  maximum_missing_terrain_share: 0.2
  random_qa_sample_size: 10
""",
        encoding="utf-8",
    )


def test_real_like_direct_multitile_pipeline(tmp_path: Path) -> None:
    create_real_like_project(tmp_path)
    inspection = inspect_region_inputs("real_like", tmp_path)
    assert inspection["coastline"]["mode"] == "direct"
    assert inspection["elevation"]["intersecting_tile_count"] == 2
    manifest = build_region(
        "real_like", root=tmp_path, force=True, write_samples=True, skip_qa_map=True
    )
    assert manifest.status == "success"
    processed = tmp_path / "data/processed/real_like"
    features = pd.read_parquet(processed / "terrain_features.parquet")
    segments = gpd.read_parquet(processed / "coast_segments.parquet")
    assert len(segments) > 10
    assert segments.orientation_source_mismatch_flag.any()
    assert (features.terrain_origin_method == "shifted_inland").any()
    assert not (features.terrain_origin_elevation_m.fillna(1) == 0).all()
    assert (tmp_path / "data/interim/real_like/coastline_source_audit.parquet").is_file()
    qa = json.loads((tmp_path / "outputs/qa/real_like/qa_summary.json").read_text())
    assert qa["passed"]
    assert qa["terrain_tiles"]["selected"] == 2
    assert qa["coastline_qa"]["source_class_review"]["rejected_class_counts"]
    assert qa["orientation_endpoint_failures"]["inland_endpoints_not_on_land"] >= 0
    assert qa["terrain_qa"]["total_transect_count"] > 0
    assert qa["cross_source_qa"]["coastline_to_nearest_valid_inland_dem_sample_m"]["method"]
