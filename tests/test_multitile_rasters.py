from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from coastscan.exceptions import RasterValidationError
from coastscan.io.rasters import inspect_raster_tiles, prepare_terrain


def write_tile(
    path: Path,
    *,
    left: float,
    top: float = 10,
    pixel: float = 1,
    value: float = 1,
    crs: str = "EPSG:3857",
    nodata_edge: bool = False,
) -> Path:
    values = np.full((10, 10), value, dtype="float32")
    if nodata_edge:
        values[:, -1] = -9999
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(left, top, pixel, pixel),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
        dataset.update_tags(1, VERTICAL_UNITS="metres")
    return path


def test_adjacent_tiles_are_windowed_into_reproducible_cache(tmp_path: Path) -> None:
    first = write_tile(tmp_path / "a.tif", left=0, value=10, nodata_edge=True)
    second = write_tile(tmp_path / "b.tif", left=10, value=20)
    output = tmp_path / "out"
    prepared = prepare_terrain(
        [first, second], box(2, 1, 18, 9), "EPSG:3857", output, 3, force=True
    )
    assert prepared.selected_tile_count == 2
    assert prepared.clipped_dimensions == (16, 8)
    assert prepared.clipped_uncompressed_bytes < prepared.selected_uncompressed_bytes
    assert prepared.mosaic_descriptor_path.is_file()
    with rasterio.open(prepared.dem_path) as dataset:
        values = dataset.read(1, masked=True)
        assert values[:, 0].mean() == pytest.approx(10)
        assert values[:, -1].mean() == pytest.approx(20)
        assert not values.mask[:, 7:10].all()
    cached = prepare_terrain([first, second], box(2, 1, 18, 9), "EPSG:3857", output, 3, force=False)
    assert cached.cache_used
    assert cached.cache_key == prepared.cache_key


def test_overlap_uses_sorted_first_valid_tile(tmp_path: Path) -> None:
    second = write_tile(tmp_path / "b.tif", left=0, value=20)
    first = write_tile(tmp_path / "a.tif", left=0, value=10)
    prepared = prepare_terrain(
        [second, first], box(1, 1, 9, 9), "EPSG:3857", tmp_path / "out", 3, force=True
    )
    with rasterio.open(prepared.dem_path) as dataset:
        assert dataset.read(1, masked=True).mean() == pytest.approx(10)


def test_only_intersecting_tiles_are_selected(tmp_path: Path) -> None:
    near = write_tile(tmp_path / "near.tif", left=0)
    far = write_tile(tmp_path / "far.tif", left=1000)
    available, selected = inspect_raster_tiles(
        [near, far], box(0, 0, 10, 10), "EPSG:3857", "metres"
    )
    assert len(available) == 2
    assert [info.path for info in selected] == [near]


def test_mixed_resolution_is_rejected(tmp_path: Path) -> None:
    first = write_tile(tmp_path / "one.tif", left=0, pixel=1)
    second = write_tile(tmp_path / "two.tif", left=0, pixel=2)
    with pytest.raises(RasterValidationError, match="inconsistent pixel resolutions"):
        inspect_raster_tiles([first, second], box(0, 0, 10, 10), "EPSG:3857", "metres")


def test_mixed_crs_is_rejected(tmp_path: Path) -> None:
    projected = write_tile(tmp_path / "projected.tif", left=0, crs="EPSG:3857")
    geographic = write_tile(
        tmp_path / "geographic.tif",
        left=0,
        top=0.0001,
        pixel=0.00001,
        crs="EPSG:4326",
    )
    with pytest.raises(RasterValidationError, match="mixed CRS"):
        inspect_raster_tiles([projected, geographic], box(0, 0, 10, 10), "EPSG:3857", "metres")


def test_source_checksum_change_invalidates_cache(tmp_path: Path) -> None:
    tile = write_tile(tmp_path / "tile.tif", left=0)
    output = tmp_path / "out"
    first = prepare_terrain([tile], box(1, 1, 9, 9), "EPSG:3857", output, 3, force=True)
    with rasterio.open(tile, "r+") as dataset:
        values = dataset.read(1)
        values[0, 0] = 99
        dataset.write(values, 1)
    second = prepare_terrain([tile], box(1, 1, 9, 9), "EPSG:3857", output, 3, force=False)
    assert not second.cache_used
    assert second.cache_key != first.cache_key
