from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString, box

from coastscan.coastline.direct import load_direct_coastline
from coastscan.coastline.segment import segment_coastline
from coastscan.exceptions import InvalidGeometryError
from coastscan.models.region import AttributeFilter


def write_direct_source(path: Path, *, polygon: bool = False) -> Path:
    geometry = (
        [box(0, 0, 100, 20)]
        if polygon
        else [
            MultiLineString([[(0, 0), (50, 2)], [(50, 2), (100, 0)]]),
            LineString([(0, -3), (100, -3)]),
            LineString([(0, 40), (100, 40)]),
        ]
    )
    attributes = (
        {"LOCALID": ["polygon"], "CLASS": ["high"], "PLEAMAR": [True]}
        if polygon
        else {
            "LOCALID": ["high_1", "low_1", "river_1"],
            "CLASS": ["high", "low", "river"],
            "PLEAMAR": [True, False, False],
        }
    )
    gpd.GeoDataFrame(attributes, geometry=geometry, crs="EPSG:3857").to_file(path, driver="GeoJSON")
    return path


def load(path: Path):
    return load_direct_coastline(
        path,
        layer=None,
        analysis_crs="EPSG:3857",
        aoi=box(10, -10, 90, 20),
        region_id="direct",
        source_id="official",
        source_checksum="abc",
        processing_version="v1",
        feature_filters=[AttributeFilter(field="PLEAMAR", accepted_values=[True])],
        source_id_field="LOCALID",
        source_class_field="CLASS",
        duplicate_tolerance_m=5,
    )


def test_direct_multiline_filter_clip_audit_and_provenance(tmp_path: Path) -> None:
    result = load(write_direct_source(tmp_path / "coast.geojson"))
    assert len(result.coastline) == 2
    assert set(result.coastline.source_feature_id) == {"high_1"}
    assert result.stats.selected_feature_count == 1
    assert result.stats.clipped_feature_count == 2
    assert result.stats.suspected_duplicate_count == 1
    assert set(result.audit.columns) >= {
        "LOCALID",
        "CLASS",
        "selected_for_analysis",
        "selection_reason",
        "original_length_m",
        "clipped_length_m",
    }
    assert result.coastline.total_bounds.tolist() == pytest.approx([10, 0.4, 90, 2])


def test_direct_polygon_is_rejected(tmp_path: Path) -> None:
    path = write_direct_source(tmp_path / "polygon.geojson", polygon=True)
    with pytest.raises(InvalidGeometryError, match="requires line geometry"):
        load(path)


def test_missing_filter_field_is_actionable(tmp_path: Path) -> None:
    path = write_direct_source(tmp_path / "coast.geojson")
    with pytest.raises(InvalidGeometryError, match="MISSING"):
        load_direct_coastline(
            path,
            layer=None,
            analysis_crs="EPSG:3857",
            aoi=box(0, -10, 100, 20),
            region_id="direct",
            source_id="official",
            source_checksum="abc",
            processing_version="v1",
            feature_filters=[AttributeFilter(field="MISSING", accepted_values=[True])],
            source_id_field="LOCALID",
            source_class_field="CLASS",
            duplicate_tolerance_m=5,
        )


def test_direct_geometry_produces_stable_segment_ids(tmp_path: Path) -> None:
    coast = load(write_direct_source(tmp_path / "coast.geojson")).coastline
    arguments = {
        "coastline": coast,
        "region_id": "direct",
        "coastline_version": "fixed",
        "target_length_m": 25,
        "minimum_length_m": 8,
    }
    first = segment_coastline(**arguments)
    second = segment_coastline(**arguments)
    assert first.segment_id.tolist() == second.segment_id.tolist()
    assert first.geometry.to_wkb().tolist() == second.geometry.to_wkb().tolist()
