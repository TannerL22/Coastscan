from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from coastscan.exceptions import ViewerError
from coastscan.viewer.validation import (
    validate_display_line_geometry,
    validate_line_geometry,
    validate_transect_geometry,
)


def mallorca_aoi() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["pilot"]},
        geometry=[
            Polygon(
                [
                    (2.66, 39.77),
                    (2.80, 39.77),
                    (2.80, 39.86),
                    (2.66, 39.86),
                    (2.66, 39.77),
                ]
            )
        ],
        crs="EPSG:4326",
    )


def mallorca_segments() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"segment_id": ["a", "b"]},
        geometry=[
            LineString([(2.67, 39.79), (2.672, 39.791)]),
            LineString([(2.74, 39.82), (2.743, 39.821)]),
        ],
        crs="EPSG:4326",
    ).to_crs("EPSG:25831")


def test_mallorca_geometry_passes_aoi_and_display_is_separate() -> None:
    analytical = mallorca_segments()
    before_crs = analytical.crs
    before_wkb = analytical.geometry.to_wkb().tolist()
    display, result = validate_line_geometry(
        analytical,
        label="Mallorca fixture",
        aoi=mallorca_aoi(),
    )
    assert display.crs.to_epsg() == 4326
    assert result.feature_count == 2
    assert result.out_of_aoi_count == 0
    assert result.aoi_bounds_wgs84 == pytest.approx((2.66, 39.77, 2.8, 39.86))
    assert analytical.crs == before_crs
    assert analytical.geometry.to_wkb().tolist() == before_wkb


def test_small_aoi_rejects_continent_spanning_geometry() -> None:
    frame = gpd.GeoDataFrame(
        {"segment_id": ["corrupt"]},
        geometry=[LineString([(2.7, 39.8), (12.7, 49.8)])],
        crs="EPSG:4326",
    ).to_crs("EPSG:25831")
    with pytest.raises(ViewerError, match="outside the AOI|materially larger"):
        validate_line_geometry(
            frame,
            label="Corrupt regional geometry",
            aoi=mallorca_aoi(),
            maximum_coordinate_jump_m=5_000_000,
            maximum_geometry_length_m=5_000_000,
        )


def test_incorrectly_declared_crs_fails_aoi_validation() -> None:
    frame = gpd.GeoDataFrame(
        {"segment_id": ["wrong-crs"]},
        geometry=[LineString([(2.70, 39.80), (2.71, 39.81)])],
        crs="EPSG:25831",
    )
    with pytest.raises(ViewerError, match="outside the AOI"):
        validate_line_geometry(frame, label="Wrong CRS", aoi=mallorca_aoi())


def test_missing_crs_and_invalid_longitude_latitude_fail() -> None:
    missing = gpd.GeoDataFrame(
        {"segment_id": ["missing"]},
        geometry=[LineString([(0, 0), (1, 1)])],
    )
    with pytest.raises(ViewerError, match="no CRS metadata"):
        validate_line_geometry(missing, label="Missing CRS")

    longitude = gpd.GeoDataFrame(
        {"segment_id": ["longitude"]},
        geometry=[LineString([(181, 0), (181.1, 0.1)])],
        crs="EPSG:4326",
    )
    with pytest.raises(ViewerError, match="longitude"):
        validate_display_line_geometry(longitude, label="Longitude")

    latitude = gpd.GeoDataFrame(
        {"segment_id": ["latitude"]},
        geometry=[LineString([(0, 91), (0.1, 91.1)])],
        crs="EPSG:4326",
    )
    with pytest.raises(ViewerError, match="latitude"):
        validate_display_line_geometry(latitude, label="Latitude")


def test_transects_are_independent_near_parents_and_reject_large_jumps() -> None:
    parents = mallorca_segments().iloc[[0]].copy()
    parent = parents.geometry.iloc[0]
    start = parent.interpolate(0.5, normalized=True)
    valid = gpd.GeoDataFrame(
        {
            "bathymetry_transect_id": ["t1", "t2"],
            "segment_id": ["a", "a"],
        },
        geometry=[
            LineString([(start.x, start.y), (start.x + 1_000, start.y)]),
            LineString([(start.x, start.y), (start.x, start.y - 1_000)]),
        ],
        crs=parents.crs,
    )
    display, result = validate_transect_geometry(
        valid,
        parents,
        ambiguous_segment_ids=set(),
        maximum_length_m=1_200,
    )
    assert len(display) == 2
    assert result.maximum_coordinate_jump_m == pytest.approx(1_000)

    invalid = valid.iloc[[0]].copy()
    invalid.geometry = [LineString([(start.x, start.y), (start.x + 10_000, start.y)])]
    with pytest.raises(ViewerError, match="exceeding"):
        validate_transect_geometry(
            invalid,
            parents,
            ambiguous_segment_ids=set(),
            maximum_length_m=1_200,
        )

    with pytest.raises(ViewerError, match="ambiguous"):
        validate_transect_geometry(
            valid,
            parents,
            ambiguous_segment_ids={"a"},
            maximum_length_m=1_200,
        )
