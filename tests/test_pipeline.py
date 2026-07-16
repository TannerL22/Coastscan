import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from coastscan.catalog.manifests import sha256_file
from coastscan.exceptions import MissingInputError
from coastscan.pipeline.build_region import build_region, inspect_region_inputs


def test_inspect_inputs_reports_metadata(synthetic_project: Path) -> None:
    result = inspect_region_inputs("demo", synthetic_project)
    assert result["land"]["feature_count"] == 1
    assert result["elevation"]["resolution"] == [5.0, 5.0]


def test_missing_real_inputs_fail_actionably(tmp_path: Path) -> None:
    (tmp_path / "config" / "regions").mkdir(parents=True)
    source = Path(__file__).parents[1] / "config" / "regions" / "mallorca_pilot.yml"
    (tmp_path / "config" / "regions" / "mallorca_pilot.yml").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(MissingInputError, match="Missing required land polygon"):
        inspect_region_inputs("mallorca_pilot", tmp_path)


def test_end_to_end_outputs_manifest_qa_and_visuals(synthetic_project: Path) -> None:
    manifest = build_region("demo", root=synthetic_project, force=True, write_samples=True)
    assert manifest.status == "success"
    expected = [
        "data/interim/demo/coastline_clean.parquet",
        "data/interim/demo/dem_analysis_crs.tif",
        "data/interim/demo/slope_degrees.tif",
        "data/interim/demo/roughness.tif",
        "data/interim/demo/terrain_samples.parquet",
        "data/processed/demo/coast_segments.parquet",
        "data/processed/demo/transects.parquet",
        "data/processed/demo/terrain_features.parquet",
        "data/processed/demo/segment_features.parquet",
        "outputs/qa/demo/qa_summary.json",
        "outputs/qa/demo/regional_overview.png",
        "outputs/qa/demo/orientation_qa.png",
        "outputs/qa/demo/terrain_cross_sections.png",
        "outputs/qa/demo/feature_distributions.png",
        "outputs/reports/demo/phase1_qa_report.html",
    ]
    assert all((synthetic_project / path).is_file() for path in expected)
    qa = json.loads((synthetic_project / "outputs/qa/demo/qa_summary.json").read_text())
    assert qa["passed"]
    segments = gpd.read_parquet(synthetic_project / "data/processed/demo/coast_segments.parquet")
    features = pd.read_parquet(synthetic_project / "data/processed/demo/terrain_features.parquet")
    assert len(segments) == 12
    assert (features.terrain_quality_flag == "good").all()
    manifest_file = next((synthetic_project / "outputs/manifests/demo").glob("*.json"))
    data = json.loads(manifest_file.read_text())
    for field in (
        "run_id",
        "configuration_checksum",
        "input_checksums",
        "input_crs",
        "input_resolutions",
        "output_checksums",
        "feature_counts",
        "quality_results",
        "software_versions",
    ):
        assert field in data


def test_repeated_build_has_stable_processed_values(synthetic_project: Path) -> None:
    build_region("demo", root=synthetic_project, force=True, skip_qa_map=True)
    segment_path = synthetic_project / "data/processed/demo/coast_segments.parquet"
    feature_path = synthetic_project / "data/processed/demo/terrain_features.parquet"
    first_segments = gpd.read_parquet(segment_path)
    first_features = pd.read_parquet(feature_path)
    build_region("demo", root=synthetic_project, force=False, skip_qa_map=True)
    second_segments = gpd.read_parquet(segment_path)
    second_features = pd.read_parquet(feature_path)
    assert first_segments.segment_id.tolist() == second_segments.segment_id.tolist()
    assert first_segments.geometry.to_wkb().tolist() == second_segments.geometry.to_wkb().tolist()
    pd.testing.assert_frame_equal(first_features, second_features, check_exact=False, rtol=1e-12)
    assert sha256_file(segment_path)
