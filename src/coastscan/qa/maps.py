"""Static, deterministic visual QA artefacts."""

import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
from rasterio import open as open_raster
from shapely.geometry import MultiPolygon, Polygon, box

os.environ.setdefault("MPLCONFIGDIR", str(Path.cwd() / ".matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def generate_qa_artifacts(
    land: Polygon | MultiPolygon,
    coastline: gpd.GeoDataFrame,
    segments: gpd.GeoDataFrame,
    transects: gpd.GeoDataFrame,
    samples: pd.DataFrame,
    features: pd.DataFrame,
    dem_path: Path,
    output_dir: Path,
    sample_size: int,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    figure, axis = plt.subplots(figsize=(9, 7))
    gpd.GeoSeries([land], crs=segments.crs).plot(ax=axis, alpha=0.25)
    coastline.plot(ax=axis, linewidth=0.8)
    segments.boundary.plot(ax=axis, linewidth=0.3)
    ambiguous = segments.loc[segments.orientation_status == "ambiguous"]
    if len(ambiguous):
        ambiguous.plot(ax=axis, linewidth=2)
    with open_raster(dem_path) as dem:
        coverage = gpd.GeoSeries([box(*dem.bounds)], crs=dem.crs).boundary
        coverage.plot(ax=axis, linestyle="--", linewidth=1)
    label_step = max(1, len(segments) // 20)
    for _, row in segments.iloc[::label_step].iterrows():
        axis.text(row.midpoint_x, row.midpoint_y, str(row.segment_number), fontsize=5)
    axis.set_title("Regional overview: land, coastline, segments and DEM coverage")
    axis.set_aspect("equal")
    path = output_dir / "regional_overview.png"
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    outputs.append(path)

    selected = segments.sample(min(sample_size, len(segments)), random_state=0).sort_values(
        "segment_id"
    )
    figure, axis = plt.subplots(figsize=(9, 7))
    gpd.GeoSeries([land], crs=segments.crs).plot(ax=axis, alpha=0.2)
    for status, group in selected.groupby("orientation_status"):
        group.plot(ax=axis, linewidth=2, label=str(status))
    segment_ids = set(selected.segment_id)
    selected_transects = transects.loc[transects.segment_id.isin(segment_ids)]
    for direction, group in selected_transects.groupby("direction"):
        group.plot(ax=axis, linewidth=0.5, label=f"{direction} transect")
    gpd.GeoSeries(selected.land_test_point, crs=segments.crs).plot(
        ax=axis, markersize=8, label="land test point"
    )
    gpd.GeoSeries(selected.sea_test_point, crs=segments.crs).plot(
        ax=axis, markersize=8, label="sea test point"
    )
    axis.set_title("Orientation QA sample: test points and transects")
    axis.set_aspect("equal")
    axis.legend(fontsize=7)
    path = output_dir / "orientation_qa.png"
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    outputs.append(path)

    figure, axes = plt.subplots(1, 2, figsize=(12, 4))
    candidates = samples[["segment_id", "transect_id"]].drop_duplicates()
    cross_section_segment_ids = candidates.segment_id.drop_duplicates()
    selection_indexes = np.linspace(
        0,
        len(cross_section_segment_ids) - 1,
        min(5, len(cross_section_segment_ids)),
        dtype=int,
    )
    selected_segment_ids = cross_section_segment_ids.iloc[selection_indexes]
    selected_cross_sections = [
        candidates.loc[candidates.segment_id == segment_id].transect_id.iloc[
            len(candidates.loc[candidates.segment_id == segment_id]) // 2
        ]
        for segment_id in selected_segment_ids
    ]
    for transect_id in selected_cross_sections:
        group = samples.loc[samples.transect_id == transect_id]
        label = f"segment={group.segment_id.iloc[0]}\ntransect={transect_id}"
        axes[0].plot(group.sample_distance_m, group.elevation_m, label=label)
        axes[1].plot(group.sample_distance_m, group.slope_deg, label=label)
    axes[0].set(
        xlabel="Distance inland (m)", ylabel="Elevation (m)", title="Terrain cross-sections"
    )
    axes[1].set(
        xlabel="Distance inland (m)", ylabel="Slope (degrees)", title="Slope cross-sections"
    )
    axes[0].legend(fontsize=4)
    path = output_dir / "terrain_cross_sections.png"
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    outputs.append(path)

    plot_data = {
        "Segment length": segments.segment_length_m,
        "Relief 50 m": features.get("land_relief_50m_p50_m", pd.Series(dtype=float)),
        "Slope p90": features.slope_p90_deg,
        "Steep sample share": features.steep_sample_share,
        "Terrain valid share": features.terrain_valid_sample_share,
    }
    figure, axes = plt.subplots(2, 3, figsize=(12, 7))
    for axis, (title, values) in zip(axes.flat, plot_data.items(), strict=False):
        axis.hist(pd.Series(values).dropna(), bins=15)
        axis.set_title(title)
    axes.flat[-1].axis("off")
    path = output_dir / "feature_distributions.png"
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    outputs.append(path)
    return outputs
