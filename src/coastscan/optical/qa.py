"""Weakness-focused Phase 3 summaries and static QA figures."""

from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coastscan.optical.masks import build_masks
from coastscan.optical.observations import load_scene_arrays


def _quantiles(values: pd.Series) -> dict[str, float | None]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return {"minimum": None, "p10": None, "p50": None, "p90": None, "maximum": None}
    return {
        "minimum": float(numeric.min()),
        "p10": float(numeric.quantile(0.1)),
        "p50": float(numeric.quantile(0.5)),
        "p90": float(numeric.quantile(0.9)),
        "maximum": float(numeric.max()),
    }


def _counts(values: pd.Series) -> dict[str, int]:
    return {str(key): int(value) for key, value in values.fillna("missing").value_counts().items()}


def optical_qa_summary(
    catalogue: pd.DataFrame,
    zones: gpd.GeoDataFrame,
    observations: pd.DataFrame,
    seasonal: pd.DataFrame,
    clarity: pd.DataFrame,
    *,
    timings: dict[str, float],
) -> dict[str, Any]:
    selected = catalogue.loc[catalogue.selected.astype(bool)]
    valid = observations.loc[observations.valid.astype(bool)]
    mask_fields = [
        field
        for field in (
            "cloud_excluded_share",
            "shadow_excluded_share",
            "glint_excluded_share",
            "land_excluded_share",
            "whitewater_excluded_share",
        )
        if field in observations
    ]
    by_month_year = (
        selected.groupby(["year", "month"], sort=True)
        .size()
        .rename("scenes")
        .reset_index()
        .to_dict(orient="records")
    )
    valid_by_month = (
        valid.groupby("month", sort=True).scene_id.nunique().rename("scenes").to_dict()
        if not valid.empty
        else {}
    )
    segment_valid_counts = (
        valid.groupby("segment_id").scene_id.nunique()
        if not valid.empty
        else pd.Series(dtype=float)
    )
    zone_segment_ids = set(zones.segment_id.dropna().astype(str))
    clarity_segment_ids = set(clarity.segment_id.dropna().astype(str))
    failed_checks: list[str] = []
    if selected.empty:
        failed_checks.append("no_selected_scenes")
    if zones.empty:
        failed_checks.append("no_segment_zones")
    if clarity.segment_id.duplicated().any():
        failed_checks.append("duplicate_clarity_segment_ids")
    if clarity_segment_ids != zone_segment_ids:
        failed_checks.append("clarity_segment_coverage_mismatch")
    return {
        "passed": not failed_checks,
        "failed_checks": failed_checks,
        "candidate_scenes": len(catalogue),
        "selected_scenes": len(selected),
        "rejected_scenes": int((~catalogue.selected.astype(bool)).sum()),
        "rejection_reasons": _counts(
            catalogue.loc[~catalogue.selected.astype(bool), "selection_reason"]
        ),
        "selected_scene_counts_by_month_year": by_month_year,
        "selected_catalogue_cloud_cover_percent": _quantiles(
            selected.catalogue_cloud_cover_percent
        ),
        "processing_baselines": _counts(selected.processing_baseline),
        "tiles": sorted(selected.tile_id.dropna().astype(str).unique()),
        "segment_zone_count": len(zones),
        "zone_statuses": _counts(zones.zone_status),
        "candidate_observations": len(observations),
        "valid_observations": int(observations.valid.astype(bool).sum()),
        "invalid_observations": int((~observations.valid.astype(bool)).sum()),
        "invalid_reasons": _counts(
            observations.loc[~observations.valid.astype(bool), "invalid_reason"]
        ),
        "valid_scene_counts_by_month": {
            str(key): int(value) for key, value in valid_by_month.items()
        },
        "valid_scene_count_by_segment": _quantiles(segment_valid_counts),
        "valid_pixel_share": _quantiles(observations.valid_pixel_share),
        "mask_burdens": {field: _quantiles(observations[field]) for field in mask_fields},
        "clarity_percentile": _quantiles(observations.clarity_percentile),
        "seasonal_clarity_percentile_p50": _quantiles(seasonal.clarity_percentile_p50),
        "clear_water_observation_share": _quantiles(seasonal.clear_water_observation_share),
        "clarity_persistence": _quantiles(seasonal.clarity_persistence),
        "clarity_variability_iqr": _quantiles(seasonal.clarity_variability_iqr),
        "best_month_distribution": _counts(clarity.best_month),
        "confidence_classes": _counts(clarity.clarity_data_confidence),
        "quality_classes": _counts(clarity.clarity_quality_flag),
        "bottom_texture_status": (
            _counts(seasonal.bottom_texture_status)
            if "bottom_texture_status" in seasonal
            else {"insufficient": len(seasonal)}
        ),
        "timings_seconds": timings,
        "interpretation_boundary": (
            "Historical region-relative coastal-water screening only; no current condition, "
            "physical visibility depth, underwater clearance, suitability, ranking or safety claim."
        ),
    }


def _save_current(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=160, bbox_inches="tight")
    plt.close()
    return path


def _metric_map(
    segments: gpd.GeoDataFrame,
    features: pd.DataFrame,
    field: str,
    title: str,
    path: Path,
) -> Path | None:
    if field not in features or not features[field].notna().any():
        return None
    joined = segments[["segment_id", segments.geometry.name]].merge(
        features[["segment_id", field]], on="segment_id", how="left", validate="one_to_one"
    )
    frame = gpd.GeoDataFrame(joined, geometry=segments.geometry.name, crs=segments.crs)
    figure, axis = plt.subplots(figsize=(9, 7))
    frame.plot(
        column=field,
        cmap="viridis",
        linewidth=2.2,
        legend=True,
        ax=axis,
        missing_kwds={"color": "#bbbbbb"},
    )
    axis.set_title(title)
    axis.set_axis_off()
    figure.patch.set_facecolor("white")
    return _save_current(path)


def _categorical_map(
    segments: gpd.GeoDataFrame,
    features: pd.DataFrame,
    field: str,
    title: str,
    path: Path,
) -> Path | None:
    if field not in features or not features[field].notna().any():
        return None
    categories = {
        "insufficient": 0,
        "low": 1,
        "moderate": 2,
        "high": 3,
    }
    mapped = features[["segment_id", field]].copy()
    mapped["_category_code"] = mapped[field].astype(str).map(categories)
    return _metric_map(segments, mapped, "_category_code", title, path)


def generate_optical_qa_figures(
    catalogue: pd.DataFrame,
    segments: gpd.GeoDataFrame,
    zones: gpd.GeoDataFrame,
    seasonal: pd.DataFrame,
    clarity: pd.DataFrame,
    output_directory: Path,
) -> list[Path]:
    """Create derived-vector/statistical QA only; never writes source-image previews."""
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    selected = catalogue.loc[catalogue.selected.astype(bool)]
    figure, axes = plt.subplots(1, 3, figsize=(14, 4))
    counts = selected.groupby(["year", "month"]).size().unstack(fill_value=0)
    counts.plot(kind="bar", stacked=True, ax=axes[0], title="Selected scenes by year and month")
    axes[1].hist(selected.catalogue_cloud_cover_percent, bins=20, color="#4078a8")
    axes[1].set_title("Catalogue cloud cover")
    axes[1].set_xlabel("Percent")
    selected.processing_baseline.value_counts().sort_index().plot(
        kind="bar", ax=axes[2], title="Processing baselines", color="#7556a8"
    )
    outputs.append(_save_current(output_directory / "scene_catalogue_qa.png"))

    figure, axis = plt.subplots(figsize=(9, 7))
    segments.plot(ax=axis, color="black", linewidth=1.0)
    colors = {"nearshore": "#2b8cbe", "coastal": "#7bccc4", "context": "#bae4bc"}
    for zone_type, group in zones.dropna(subset=[zones.geometry.name]).groupby("zone_type"):
        group.plot(
            ax=axis, color=colors.get(str(zone_type), "#aaaaaa"), alpha=0.35, label=str(zone_type)
        )
    axis.legend()
    axis.set_title("Segment-owned optical zones and exclusions")
    axis.set_axis_off()
    outputs.append(_save_current(output_directory / "optical_zones_qa.png"))

    fields = [
        "valid_scene_count",
        "clarity_percentile_p50",
        "clear_water_observation_share",
        "clarity_persistence",
        "clarity_variability_iqr",
    ]
    figure, axes = plt.subplots(2, 3, figsize=(14, 8))
    for axis, field in zip(axes.flat, fields, strict=False):
        values = pd.to_numeric(clarity[field], errors="coerce").dropna()
        axis.hist(values, bins=18, color="#3182bd")
        axis.set_title(field.replace("_", " "))
    axes.flat[-1].axis("off")
    outputs.append(_save_current(output_directory / "clarity_distributions.png"))

    for field, title in (
        ("clarity_percentile_p50", "Historical median relative clarity percentile"),
        ("clear_water_observation_share", "Clear-looking observation share"),
        ("clarity_persistence", "Historical clarity persistence"),
        ("valid_scene_count", "Valid optical scene count"),
        ("apparent_bottom_texture_persistence", "Apparent bottom-texture persistence"),
    ):
        path = _metric_map(segments, clarity, field, title, output_directory / f"map_{field}.png")
        if path is not None:
            outputs.append(path)

    confidence_path = _categorical_map(
        segments,
        clarity,
        "clarity_data_confidence",
        "Clarity data confidence (0 insufficient, 1 low, 2 moderate, 3 high)",
        output_directory / "map_clarity_data_confidence.png",
    )
    if confidence_path is not None:
        outputs.append(confidence_path)

    period_ids = ("june", "july", "august", "september", "extended_summer_may_sep")
    for period_id in period_ids:
        period = seasonal.loc[
            (seasonal.period_id.astype(str) == period_id)
            & (seasonal.zone_type.astype(str) == "nearshore")
        ]
        path = _metric_map(
            segments,
            period,
            "clarity_percentile_p50",
            f"{period_id.replace('_', ' ').title()} relative clarity percentile",
            output_directory / f"map_clarity_{period_id}.png",
        )
        if path is not None:
            outputs.append(path)
    return outputs


def _rgb(arrays: dict[str, np.ndarray]) -> np.ndarray:
    image = np.dstack([arrays["red"], arrays["green"], arrays["blue"]]).astype(float)
    finite = image[np.isfinite(image)]
    if not finite.size:
        return np.zeros_like(image)
    low, high = np.nanpercentile(finite, [2, 98])
    if high <= low:
        return np.zeros_like(image)
    return np.asarray(np.clip((image - low) / (high - low), 0, 1))


def generate_optical_mask_qa(
    catalogue: pd.DataFrame,
    cache_directory: Path,
    output_directory: Path,
    *,
    maximum_scenes: int = 3,
) -> list[Path]:
    """Write small local representative RGB/mask panels from acquired official clips."""
    selected = catalogue.loc[catalogue.selected.astype(bool)].sort_values(
        ["acquisition_datetime_utc", "scene_id"]
    )
    if selected.empty:
        return []
    positions = sorted(set(np.linspace(0, len(selected) - 1, maximum_scenes).astype(int)))
    outputs: list[Path] = []
    for position in positions:
        scene = selected.iloc[position]
        arrays = load_scene_arrays(scene, cache_directory)
        masks = build_masks(
            arrays["blue"],
            arrays["green"],
            arrays["red"],
            arrays["nir"],
            arrays["swir1"],
            arrays["scene_classification"].astype("uint8"),
        )
        figure, axes = plt.subplots(2, 4, figsize=(14, 7))
        panels = (
            (_rgb(arrays), "RGB overview", None),
            (masks.spectral_water, "Spectral-water mask", "Blues"),
            (masks.cloud | masks.cirrus, "Cloud/cirrus mask", "Greys"),
            (masks.shadow | masks.dark_shadow, "Shadow mask", "Greys"),
            (masks.glint_risk, "Glint-risk mask", "Oranges"),
            (masks.whitewater, "Whitewater mask", "Purples"),
            (masks.land, "SCL land/invalid mask", "Greens"),
            (masks.valid_water, "Final valid-water mask", "Blues"),
        )
        for axis, (values, title, cmap) in zip(axes.flat, panels, strict=True):
            axis.imshow(values, cmap=cmap, interpolation="nearest")
            axis.set_title(title)
            axis.set_axis_off()
        figure.suptitle(f"Representative optical masks: {scene.scene_id}")
        safe_id = "".join(
            character if character.isalnum() else "_" for character in str(scene.scene_id)
        )
        outputs.append(_save_current(output_directory / f"mask_qa_{safe_id}.png"))
    return outputs


def generate_optical_time_series_qa(
    observations: pd.DataFrame,
    clarity: pd.DataFrame,
    output_directory: Path,
) -> Path | None:
    """Plot deterministic weakness-focused representative segment histories."""
    valid = observations.loc[
        observations.valid.astype(bool) & observations.clarity_percentile.notna()
    ].copy()
    if valid.empty:
        return None
    summary = valid.groupby("segment_id", sort=True).clarity_percentile.agg(
        ["median", "std", "count"]
    )
    choices: list[tuple[str, str]] = []

    def add(label: str, segment_id: object) -> None:
        value = str(segment_id)
        if all(existing != value for _, existing in choices):
            choices.append((label, value))

    add("highest median", summary["median"].idxmax())
    if summary["std"].notna().any():
        add("highest variability", summary["std"].idxmax())
    zero = pd.Series(0.0, index=observations.index)
    burdens = (
        observations.assign(
            exclusion_burden=pd.to_numeric(
                observations.get("glint_excluded_share", zero), errors="coerce"
            ).fillna(0)
            + pd.to_numeric(
                observations.get("shadow_excluded_share", zero), errors="coerce"
            ).fillna(0)
        )
        .groupby("segment_id")
        .exclusion_burden.mean()
    )
    if not burdens.empty:
        add("glint/shadow limited", burdens.idxmax())
    insufficient = clarity.loc[
        clarity.clarity_data_confidence.astype(str).eq("insufficient"), "segment_id"
    ]
    if not insufficient.empty:
        add("insufficient", insufficient.sort_values().iloc[0])
    candidates = observations.loc[
        observations.get(
            "apparent_bottom_texture_candidate",
            pd.Series(False, index=observations.index),
        ).fillna(False),
        "segment_id",
    ]
    if not candidates.empty:
        add("bottom-texture candidate", candidates.astype(str).sort_values().iloc[0])
    add("deterministic reference", summary.sort_index().index[0])
    choices = choices[:6]
    figure, axes = plt.subplots(len(choices), 1, figsize=(11, max(3, 2.4 * len(choices))))
    axes_array = np.atleast_1d(axes)
    for axis, (label, segment_id) in zip(axes_array, choices, strict=True):
        group = valid.loc[valid.segment_id.astype(str).eq(segment_id)].copy()
        if "acquisition_datetime_utc" in group:
            group["_x"] = pd.to_datetime(group.acquisition_datetime_utc, errors="coerce")
        else:
            group["_x"] = np.arange(len(group))
        group = group.sort_values([field for field in ("_x", "scene_id") if field in group])
        axis.plot(group["_x"], group.clarity_percentile, marker="o", linewidth=1)
        axis.set_ylim(0, 100)
        axis.set_ylabel("Percentile")
        axis.set_title(f"{label}: {segment_id}")
        axis.grid(alpha=0.25)
    axes_array[-1].set_xlabel("Acquisition time / deterministic scene order")
    return _save_current(output_directory / "representative_clarity_time_series.png")


def cross_layer_diagnostics(phase3: gpd.GeoDataFrame) -> dict[str, Any]:
    """Descriptive combinations only; deliberately no weighted score or ranking."""
    required = {"land_relief_100m_p90_m", "clarity_percentile_p50"}
    if not required.issubset(phase3.columns):
        return {"available": False}
    valid = phase3.dropna(subset=list(required)).copy()
    if valid.empty:
        return {"available": False}
    relief_cut = float(valid.land_relief_100m_p90_m.median())
    clarity_cut = float(valid.clarity_percentile_p50.median())
    high_relief = valid.land_relief_100m_p90_m >= relief_cut
    high_clarity = valid.clarity_percentile_p50 >= clarity_cut
    confidence = valid.get("clarity_data_confidence", pd.Series("insufficient", index=valid.index))
    screening = valid.get(
        "bathymetry_screening_class", pd.Series("insufficient", index=valid.index)
    )
    confidence_low = confidence.astype(str).isin({"low", "insufficient"})
    background = screening.astype(str).eq("background_only")
    return {
        "available": True,
        "descriptive_only": True,
        "no_score_or_ranking": True,
        "high_relief_high_clarity": int((high_relief & high_clarity).sum()),
        "high_relief_low_clarity": int((high_relief & ~high_clarity).sum()),
        "low_relief_high_clarity": int((~high_relief & high_clarity).sum()),
        "high_clarity_low_confidence": int((high_clarity & confidence_low).sum()),
        "high_clarity_background_only_bathymetry": int((high_clarity & background).sum()),
    }
