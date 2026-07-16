import hashlib
import io
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString

from coastscan.acquire.http import download_https_resource
from coastscan.bathymetry.adapters import canonicalize_depth, resolution_class
from coastscan.bathymetry.features import calculate_bathymetry_features
from coastscan.bathymetry.transects import generate_bathymetry_transects
from coastscan.catalog.manifests import sha256_file
from coastscan.config import load_region_config
from coastscan.exceptions import ConfigurationError, RasterValidationError
from coastscan.models.acquisition import AcquisitionResource
from coastscan.pipeline.build_bathymetry import build_bathymetry, inspect_bathymetry
from coastscan.pipeline.build_region import build_region


def test_canonical_depth_conventions_preserve_nodata_and_zero() -> None:
    source = np.array([-10.0, -0.0, 2.0, np.nan])
    converted = canonicalize_depth(source, "negative_elevation", zero_is_valid=True)
    assert converted[0] == 10
    assert converted[1] == 0
    assert np.isnan(converted[2:]).all()
    positive = canonicalize_depth(
        np.array([0.0, 12.0, -1.0, np.nan]), "positive_down", zero_is_valid=True
    )
    assert positive[:2].tolist() == [0.0, 12.0]
    assert np.isnan(positive[2:]).all()


def test_resolution_class_is_explicit() -> None:
    assert resolution_class(0.9) == "below_native_resolution"
    assert resolution_class(1.0) == "marginally_resolved"
    assert resolution_class(2.0) == "well_resolved"
    assert resolution_class(None) == "unavailable"


def test_region_without_bathymetry_remains_valid(synthetic_project: Path) -> None:
    phase1, _ = load_region_config("demo", synthetic_project)
    assert phase1.inputs.bathymetry is None


def test_valid_bathymetry_configuration(synthetic_bathymetry_project: Path) -> None:
    configured, _ = load_region_config("demo", synthetic_bathymetry_project)
    assert configured.inputs.bathymetry is not None
    assert configured.bathymetry is not None


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("    vertical_datum: SYNTHETIC_DATUM\n", "", "vertical_datum"),
        ("  target_distances_m: [100, 250, 500]", "  target_distances_m: [250, 100]", "sorted"),
        (
            "  maximum_offshore_distance_m: 500",
            "  maximum_offshore_distance_m: 200",
            "maximum_offshore_distance_m",
        ),
        ("    variables: {mean_depth: band_1}", "    variables: {}", "mean_depth"),
    ],
)
def test_invalid_bathymetry_configuration_is_actionable(
    synthetic_bathymetry_project: Path, old: str, new: str, match: str
) -> None:
    path = synthetic_bathymetry_project / "config" / "regions" / "demo.yml"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=match):
        load_region_config("demo", synthetic_bathymetry_project)


def test_acquisition_methods_are_typed() -> None:
    resource = AcquisitionResource.model_validate(
        {
            "resource_name": "subset.nc",
            "method": "emodnet_product",
            "url": "https://authority.example/subset.nc",
            "local_relative_path": "data/raw/subset.nc",
        }
    )
    assert resource.method == "emodnet_product"
    manual = AcquisitionResource.model_validate(
        {
            "resource_name": "survey.xyz",
            "method": "manual_request",
            "manual_instructions": "Request through the official data portal.",
            "local_relative_path": "data/raw/survey.xyz",
        }
    )
    assert manual.manual_instructions
    with pytest.raises(ValidationError):
        AcquisitionResource.model_validate(
            {
                "resource_name": "bad.nc",
                "method": "direct_http",
                "local_relative_path": "bad.nc",
            }
        )


class _Headers(dict[str, str]):
    def get(self, key: str, default: str = "") -> str:
        return super().get(key, default)


class _Response(io.BytesIO):
    headers = _Headers({"Content-Type": "application/octet-stream", "ETag": "fixture"})

    def geturl(self) -> str:
        return "https://authority.example/source.bin"


def test_direct_https_download_is_atomic_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"official fixture"
    checksum = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        "coastscan.acquire.http.urlopen", lambda request, timeout: _Response(payload)
    )
    destination = tmp_path / "source.bin"
    actual, reused, metadata = download_https_resource(
        "https://authority.example/source.bin", destination, expected_checksum=checksum
    )
    assert (actual, reused, metadata["etag"]) == (checksum, False, "fixture")
    _, reused, _ = download_https_resource(
        "https://authority.example/source.bin", destination, expected_checksum=checksum
    )
    assert reused
    assert not destination.with_suffix(".bin.part").exists()


def test_bathymetry_transects_are_stable_and_skip_ambiguous() -> None:
    segments = gpd.GeoDataFrame(
        {
            "segment_id": ["resolved", "ambiguous"],
            "orientation_status": ["resolved", "ambiguous"],
            "seaward_bearing_deg": [180.0, np.nan],
            "orientation_method": ["fixture", "unresolved"],
            "orientation_source_mismatch_flag": [False, False],
        },
        geometry=[LineString([(0, 0), (200, 0)]), LineString([(0, 10), (200, 10)])],
        crs="EPSG:3857",
    )
    first = generate_bathymetry_transects(segments, spacing_m=50, maximum_distance_m=500)
    second = generate_bathymetry_transects(segments, spacing_m=50, maximum_distance_m=500)
    assert first.bathymetry_transect_id.tolist() == second.bathymetry_transect_id.tolist()
    assert set(first.segment_id) == {"resolved"}
    assert np.allclose(first.length, 500)
    assert np.allclose(first.bearing_deg, 180)

    segments.loc[1, "orientation_status"] = "resolved_fallback"
    segments.loc[1, "seaward_bearing_deg"] = 180.0
    with_fallback = generate_bathymetry_transects(segments, spacing_m=50, maximum_distance_m=500)
    assert set(with_fallback.segment_id) == {"resolved", "ambiguous"}


def test_feature_aggregation_known_gradient_and_contour(
    synthetic_bathymetry_project: Path,
) -> None:
    config, _ = load_region_config("demo", synthetic_bathymetry_project)
    assert config.bathymetry is not None and config.inputs.bathymetry is not None
    segments = gpd.GeoDataFrame(
        {"segment_id": ["s1"], "orientation_status": ["resolved"]},
        geometry=[LineString([(0, 0), (100, 0)])],
        crs="EPSG:3857",
    )
    transects = gpd.GeoDataFrame(
        {
            "bathymetry_transect_id": ["t1", "t2"],
            "segment_id": ["s1", "s1"],
            "first_valid_depth_distance_m": [0.0, 50.0],
            "bathymetry_origin_status": ["exact_or_near_coast", "shifted_offshore"],
        },
        geometry=[LineString([(0, 0), (0, -500)]), LineString([(50, 0), (50, -500)])],
        crs="EPSG:3857",
    )
    rows = []
    for transect_id in ("t1", "t2"):
        for distance in (0.0, 100.0, 250.0, 500.0):
            rows.append(
                {
                    "bathymetry_transect_id": transect_id,
                    "segment_id": "s1",
                    "distance_from_coast_m": distance,
                    "depth_mean_m": 5 + distance * 0.05,
                    "depth_std_m": 1.0,
                    "observation_count": 2.0,
                    "source_reference": 7.0,
                    "source_type": "survey",
                    "quality_index": np.nan,
                    "interpolation_flag": 0.0,
                    "sample_valid": True,
                }
            )
    features = calculate_bathymetry_features(
        segments, transects, pd.DataFrame(rows), config.bathymetry, config.inputs.bathymetry
    )
    assert features.loc[0, "gradient_100_500m_p50"] == pytest.approx(0.05)
    assert features.loc[0, "distance_to_20m_depth_p50_m"] == 500
    assert features.loc[0, "bathymetry_valid_transect_share"] == 1


def test_full_synthetic_phase2_build_manifest_cache_and_upstream_contract(
    synthetic_bathymetry_project: Path,
) -> None:
    build_region("demo", root=synthetic_bathymetry_project, force=True, skip_qa_map=True)
    segment_path = synthetic_bathymetry_project / "data/processed/demo/coast_segments.parquet"
    phase1_checksum = sha256_file(segment_path)
    first = build_bathymetry(
        "demo", root=synthetic_bathymetry_project, force=True, skip_qa_map=True
    )
    first_features = pd.read_parquet(
        synthetic_bathymetry_project / "data/processed/demo/bathymetry_features.parquet"
    )
    second = build_bathymetry(
        "demo", root=synthetic_bathymetry_project, force=False, skip_qa_map=True
    )
    second_features = pd.read_parquet(
        synthetic_bathymetry_project / "data/processed/demo/bathymetry_features.parquet"
    )
    assert first.status == second.status == "success"
    assert not first.cache_used and second.cache_used
    pd.testing.assert_frame_equal(first_features, second_features)
    assert sha256_file(segment_path) == phase1_checksum
    assert inspect_bathymetry("demo", synthetic_bathymetry_project)["valid_bathymetry_cache_exists"]
    assert first.upstream_segment_checksum == phase1_checksum
    assert (
        first.output_checksums["data/processed/demo/bathymetry_features.parquet"]
        == second.output_checksums["data/processed/demo/bathymetry_features.parquet"]
    )
    manifest_path = next(
        (synthetic_bathymetry_project / "outputs/manifests/demo").glob("*_bathymetry.json")
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["pipeline_stage"] == "bathymetry_phase2"
    assert data["quality_results"]["passed"]

    changed = gpd.read_parquet(segment_path)
    changed.loc[0, "segment_id"] += "_changed"
    changed.to_parquet(segment_path, index=False)
    with pytest.raises(RasterValidationError, match="stale or changed"):
        build_bathymetry("demo", root=synthetic_bathymetry_project, skip_qa_map=True)
