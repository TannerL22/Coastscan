from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from coastscan.catalog.manifests import sha256_file
from coastscan.exceptions import ViewerError
from coastscan.viewer.data import (
    load_display_transects,
    load_viewer_data,
    missing_outputs_message,
)


def test_valid_phase2_dataset_loads_and_reprojects_without_writing(viewer_project: Path) -> None:
    source = viewer_project / "data/processed/viewer_demo/segment_features_phase2.parquet"
    before = sha256_file(source)
    data = load_viewer_data("viewer_demo", viewer_project)
    assert data.mode == "phase2"
    assert len(data.display_segments) == 12
    assert data.source_crs == "EPSG:3857"
    assert data.analytical_segments.crs.to_epsg() == 3857
    assert data.display_segments.crs.to_epsg() == 4326
    assert sha256_file(source) == before


def test_terrain_only_fallback_loads(terrain_only_viewer_project: Path) -> None:
    data = load_viewer_data("viewer_terrain_only", terrain_only_viewer_project)
    assert data.mode == "terrain_only"
    assert not data.has_bathymetry
    assert "bathymetry_screening_class" not in data.display_segments


def test_missing_outputs_are_actionable(tmp_path: Path) -> None:
    with pytest.raises(ViewerError, match="No processed CoastScan outputs") as error:
        load_viewer_data("missing_region", tmp_path)
    assert "build-region" in str(error.value)
    assert "build-bathymetry" in str(error.value)
    assert missing_outputs_message("missing_region").startswith("No processed")


def test_duplicate_segment_ids_fail_clearly(viewer_project: Path) -> None:
    path = viewer_project / "data/processed/viewer_demo/segment_features_phase2.parquet"
    frame = gpd.read_parquet(path)
    frame.loc[1, "segment_id"] = frame.loc[0, "segment_id"]
    frame.to_parquet(path, index=False)
    with pytest.raises(ViewerError, match="duplicate segment IDs"):
        load_viewer_data("viewer_demo", viewer_project)


def test_missing_crs_and_invalid_geometry_fail(viewer_project: Path) -> None:
    path = viewer_project / "data/processed/viewer_demo/segment_features_phase2.parquet"
    frame = gpd.read_parquet(path)
    frame.set_crs(None, allow_override=True).to_parquet(path, index=False)
    with pytest.raises(ViewerError, match="no CRS metadata"):
        load_viewer_data("viewer_demo", viewer_project)

    frame = gpd.read_parquet(
        Path(__file__).parents[1] / "data/fixtures/viewer_demo/segment_features_phase2.parquet"
    )
    frame.loc[0, "geometry"] = Point(0, 0)
    frame.to_parquet(path, index=False)
    with pytest.raises(ViewerError, match="requires line segment geometry"):
        load_viewer_data("viewer_demo", viewer_project)


def test_transects_are_lazy_filtered_and_exclude_ambiguous(viewer_project: Path) -> None:
    data = load_viewer_data("viewer_demo", viewer_project)
    selected = {"viewer_demo_segment_00"}
    transects = load_display_transects(data, selected)
    assert len(transects) == 2
    assert set(transects.segment_id) == selected
    assert transects.crs.to_epsg() == 4326
    all_transects = load_display_transects(data)
    assert "viewer_demo_segment_02" not in set(all_transects.segment_id)
