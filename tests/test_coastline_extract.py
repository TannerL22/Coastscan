import geopandas as gpd
import pytest
from shapely.geometry import GeometryCollection

from coastscan.coastline.extract import extract_coastline
from coastscan.exceptions import InvalidGeometryError
from coastscan.io.vectors import load_land


def extract(land, include_interior: bool = False):
    return extract_coastline(
        land,
        region_id="test",
        source_id="source",
        source_checksum="abc",
        processing_version="v1",
        crs="EPSG:3857",
        include_interior=include_interior,
    )


def test_exterior_boundary_extracted(rectangular_island) -> None:
    result = extract(rectangular_island)
    assert len(result) == 1
    assert result.shoreline_type.tolist() == ["exterior"]
    assert result.length.iloc[0] == pytest.approx(3000)


def test_interior_ring_classified_and_excluded_by_default(lake_polygon) -> None:
    assert len(extract(lake_polygon)) == 1
    included = extract(lake_polygon, True)
    assert set(included.shoreline_type) == {"exterior", "interior"}
    assert len(included) == 2


def test_multipart_islands_preserved(multipart_islands) -> None:
    result = extract(multipart_islands)
    assert len(result) == 2


def test_non_polygon_input_rejected(tmp_path) -> None:
    path = tmp_path / "bad.geojson"
    gpd.GeoDataFrame(geometry=[GeometryCollection()], crs="EPSG:3857").to_file(
        path, driver="GeoJSON"
    )
    with pytest.raises(InvalidGeometryError):
        load_land(path, None, "EPSG:3857")


def test_invalid_bowtie_is_minimally_repaired(tmp_path) -> None:
    from shapely.geometry import Polygon

    path = tmp_path / "repair.geojson"
    bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    gpd.GeoDataFrame(geometry=[bowtie], crs="EPSG:3857").to_file(path, driver="GeoJSON")
    result = load_land(path, None, "EPSG:3857")
    assert result.repair_applied
    assert result.geometry.is_valid


def test_empty_geometry_fails(tmp_path) -> None:
    path = tmp_path / "empty.geojson"
    gpd.GeoDataFrame(geometry=[None], crs="EPSG:3857").to_file(path, driver="GeoJSON")
    with pytest.raises(InvalidGeometryError):
        load_land(path, None, "EPSG:3857")
