"""Generate small deterministic synthetic processed viewer fixtures."""

from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString


def create(root: Path) -> tuple[Path, ...]:
    fixture = root / "data" / "fixtures" / "viewer_demo"
    terrain_fixture = root / "data" / "fixtures" / "viewer_terrain_only"
    fixture.mkdir(parents=True, exist_ok=True)
    terrain_fixture.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    geometries: list[LineString] = []
    for index in range(12):
        segment_id = f"viewer_demo_segment_{index:02d}"
        geometry = LineString([(280000 + index * 300, 4830000), (280250 + index * 300, 4830100)])
        orientation = (
            "ambiguous" if index == 2 else "resolved_fallback" if index == 1 else "resolved"
        )
        terrain_missing = index == 3
        bathymetry_missing = index in {2, 4}
        screening = (
            "insufficient"
            if bathymetry_missing
            else "background_only"
            if index in {5, 6, 7}
            else "regional_screening"
        )
        relief = float(5 + index * 4)
        gradient = float(-0.01 + index * 0.004)
        row: dict[str, object] = {
            "segment_id": segment_id,
            "region_id": "viewer_demo",
            "coastline_part_id": "synthetic_coastline_00",
            "segment_number": index,
            "segment_length_m": float(250 + index),
            "coastline_version": "synthetic-viewer-1",
            "orientation_status": orientation,
            "orientation_method": "synthetic_fixture",
            "orientation_source_mismatch_flag": index == 7,
            "land_relief_25m_p50_m": np.nan if terrain_missing else relief * 0.3,
            "land_relief_50m_p50_m": np.nan if terrain_missing else relief * 0.6,
            "land_relief_100m_p50_m": np.nan if terrain_missing else relief * 0.9,
            "land_relief_50m_p90_m": np.nan if terrain_missing else relief * 0.7,
            "land_relief_100m_p90_m": np.nan if terrain_missing else relief,
            "slope_p50_deg": np.nan if terrain_missing else 8.0 + index,
            "slope_p90_deg": np.nan if terrain_missing else 15.0 + index * 2,
            "slope_max_deg": np.nan if terrain_missing else 25.0 + index * 3,
            "steep_sample_share": np.nan if terrain_missing else min(index / 12, 1.0),
            "steep_nearshore_transect_share": np.nan if terrain_missing else min(index / 11, 1.0),
            "distance_to_first_steep_sample_p50_m": np.nan if terrain_missing else 10.0 + index * 3,
            "roughness_p90": np.nan if terrain_missing else 2.5,
            "terrain_valid_sample_share": 0.0 if terrain_missing else 0.75 + index * 0.02,
            "terrain_quality_flag": "outside_dem" if terrain_missing else "good",
            "bathymetry_source_id": "synthetic_regional_grid",
            "bathymetry_release": "fixture-1",
            "bathymetry_vertical_datum": "SYNTHETIC_DATUM",
            "bathymetry_native_resolution_m": 100.0,
            "bathymetry_valid_transect_share": 0.0 if bathymetry_missing else 0.55 + index * 0.03,
            "bathymetry_first_valid_distance_p50_m": np.nan
            if bathymetry_missing
            else 300.0
            if index == 5
            else 25.0 + index * 10,
            "bathymetry_first_valid_distance_p90_m": np.nan
            if bathymetry_missing
            else 75.0 + index * 12,
            "bathymetry_large_coastal_gap_share": 0.8 if index == 5 else 0.0,
            "bathymetry_screening_class": screening,
            "bathymetry_screening_reasons": '["synthetic_fixture","regional_proxy"]',
            "bathymetry_quality_flag": "insufficient"
            if bathymetry_missing
            else "usable_with_resolution_limits",
            "depth_100m_p50_m": np.nan if bathymetry_missing else 3.0 + index,
            "depth_250m_p50_m": np.nan if bathymetry_missing else 7.0 + index * 1.5,
            "depth_500m_p50_m": np.nan if bathymetry_missing else 12.0 + index * 2,
            "depth_1000m_p50_m": np.nan if bathymetry_missing else 18.0 + index * 3,
            "gradient_100_500m_p50": np.nan if bathymetry_missing else gradient * 0.8,
            "gradient_250_1000m_p50": np.nan if bathymetry_missing else gradient,
            "distance_to_5m_depth_p50_m": np.nan if bathymetry_missing else 100.0 + index * 10,
            "distance_to_10m_depth_p50_m": np.nan if bathymetry_missing else 200.0 + index * 15,
            "distance_to_20m_depth_p50_m": np.nan if bathymetry_missing else 400.0 + index * 20,
            "distance_to_30m_depth_p50_m": np.nan if bathymetry_missing else 700.0 + index * 25,
            "interpolated_cell_share": np.nan if bathymetry_missing else 0.2 + index * 0.05,
            "extrapolated_cell_share": np.nan,
            "global_fallback_source_share": np.nan
            if bathymetry_missing
            else 0.9
            if index == 6
            else 0.1,
            "survey_source_share": np.nan if bathymetry_missing else 0.0,
        }
        rows.append(row)
        geometries.append(geometry)
    phase2 = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:3857")
    coast_segments = phase2[["segment_id", "geometry"]].copy()
    coast_path = fixture / "coast_segments.parquet"
    coast_segments.to_parquet(coast_path, index=False)
    phase2_path = fixture / "segment_features_phase2.parquet"
    phase2.to_parquet(phase2_path, index=False)
    terrain_fields = [
        column
        for column in phase2.columns
        if not (
            column.startswith("bathymetry_")
            or column.startswith("depth_")
            or column.startswith("gradient_")
            or column.startswith("distance_to_")
            and "depth" in column
            or column
            in {
                "interpolated_cell_share",
                "extrapolated_cell_share",
                "global_fallback_source_share",
                "survey_source_share",
            }
        )
    ]
    terrain_path = terrain_fixture / "segment_features.parquet"
    phase2[terrain_fields].to_parquet(terrain_path, index=False)
    terrain_coast_path = terrain_fixture / "coast_segments.parquet"
    coast_segments.to_parquet(terrain_coast_path, index=False)

    transect_rows: list[dict[str, object]] = []
    for row, geometry in zip(rows, geometries, strict=True):
        if row["orientation_status"] == "ambiguous" or row["bathymetry_valid_transect_share"] == 0:
            continue
        origin = geometry.interpolate(0.5, normalized=True)
        for number in range(2):
            transect_rows.append(
                {
                    "bathymetry_transect_id": f"{row['segment_id']}_b{number:03d}",
                    "segment_id": row["segment_id"],
                    "bathymetry_origin_status": "large_coastal_gap"
                    if row["bathymetry_large_coastal_gap_share"]
                    else "exact_or_near_coast",
                    "geometry": LineString(
                        [
                            (origin.x + number * 5, origin.y),
                            (origin.x + number * 5, origin.y - 1000),
                        ]
                    ),
                }
            )
    transect_path = fixture / "bathymetry_transects.parquet"
    gpd.GeoDataFrame(transect_rows, geometry="geometry", crs="EPSG:3857").to_parquet(
        transect_path, index=False
    )
    return phase2_path, coast_path, transect_path, terrain_path, terrain_coast_path


if __name__ == "__main__":
    for created in create(Path(__file__).resolve().parents[1]):
        print(created)
