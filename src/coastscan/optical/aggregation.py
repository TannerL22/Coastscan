"""Deterministic monthly, seasonal, and headline optical aggregation."""

from collections.abc import Mapping

import numpy as np
import pandas as pd

from coastscan.optical.confidence import classify_confidence


def aggregate_periods(
    observations: pd.DataFrame,
    periods: Mapping[str, list[int]],
    *,
    clear_threshold: float,
    turbid_threshold: float,
    minimum_scenes: int,
    minimum_months: int,
    bottom_minimum_scenes: int = 8,
    bottom_minimum_persistence: float = 0.5,
) -> pd.DataFrame:
    """Aggregate valid scene-zone observations; invalid rows remain auditable upstream."""
    required = {
        "segment_id",
        "zone_type",
        "scene_id",
        "year",
        "month",
        "clarity_percentile",
        "valid",
    }
    missing = sorted(required - set(observations.columns))
    if missing:
        raise ValueError(f"Optical observations are missing: {', '.join(missing)}")
    records: list[dict[str, object]] = []
    for period_id, months in periods.items():
        subset = observations.loc[observations.month.isin(months)].copy()
        for (segment_id, zone_type), group in subset.groupby(
            ["segment_id", "zone_type"], sort=True, dropna=False
        ):
            valid = group.loc[group.valid.astype(bool) & group.clarity_percentile.notna()]
            values = pd.to_numeric(valid.clarity_percentile, errors="coerce").dropna()
            monthly_medians = (
                valid.assign(
                    clarity_numeric=pd.to_numeric(valid.clarity_percentile, errors="coerce")
                )
                .groupby(["year", "month"], sort=True)
                .clarity_numeric.median()
                .dropna()
            )
            mask_columns = [
                str(name) for name in group.columns if str(name).endswith("_excluded_share")
            ]
            burden = (
                float(group[mask_columns].fillna(0).sum(axis=1).clip(upper=1).mean())
                if mask_columns
                else 0.0
            )
            valid_share = float(len(valid) / len(group)) if len(group) else 0.0
            confidence = classify_confidence(
                valid_scenes=int(valid.scene_id.nunique()),
                valid_years=int(valid.year.nunique()),
                valid_months=int(valid.month.nunique()),
                valid_observation_share=valid_share,
                mean_mask_burden=burden,
                minimum_scenes=minimum_scenes,
                minimum_months=minimum_months,
            )
            record: dict[str, object] = {
                "segment_id": str(segment_id),
                "zone_type": str(zone_type),
                "zone_class": str(zone_type),
                "period_id": period_id,
                "period_label": period_id.replace("_", " ").title(),
                "configured_months": ",".join(map(str, months)),
                "candidate_observation_count": len(group),
                "valid_scene_count": int(valid.scene_id.nunique()),
                "valid_year_count": int(valid.year.nunique()),
                "valid_month_count": int(valid.month.nunique()),
                "valid_observation_share": valid_share,
                "clarity_percentile_p10": float(values.quantile(0.1)) if len(values) else np.nan,
                "clarity_percentile_p50": float(values.quantile(0.5)) if len(values) else np.nan,
                "clarity_percentile_p90": float(values.quantile(0.9)) if len(values) else np.nan,
                "clarity_variability_iqr": (
                    float(values.quantile(0.75) - values.quantile(0.25)) if len(values) else np.nan
                ),
                "clarity_variability_mad": (
                    float((values - values.median()).abs().median()) if len(values) else np.nan
                ),
                "clear_water_observation_share": (
                    float((values >= clear_threshold).mean()) if len(values) else np.nan
                ),
                "turbid_water_observation_share": (
                    float((values <= turbid_threshold).mean()) if len(values) else np.nan
                ),
                "clarity_persistence": (
                    float((monthly_medians >= clear_threshold).mean())
                    if len(monthly_medians)
                    else np.nan
                ),
                "mean_mask_burden": burden,
                "clarity_data_confidence": confidence.confidence,
                "clarity_quality_flag": confidence.quality_flag,
                "clarity_limitation_reasons": ";".join(confidence.reasons),
            }
            if "apparent_bottom_texture_repeatable" in valid:
                texture_share = float(
                    valid.apparent_bottom_texture_repeatable.fillna(False).astype(bool).mean()
                )
                texture_scenes = int(valid.scene_id.nunique())
                texture_status = (
                    "repeatable"
                    if texture_scenes >= bottom_minimum_scenes
                    and texture_share >= bottom_minimum_persistence
                    else "unstable"
                    if texture_scenes >= bottom_minimum_scenes
                    else "insufficient"
                )
                record.update(
                    apparent_bottom_texture_scene_share=texture_share,
                    bottom_visibility_proxy_share=(
                        texture_share if texture_status == "repeatable" else np.nan
                    ),
                    apparent_bottom_texture_persistence=(
                        texture_share if texture_status == "repeatable" else np.nan
                    ),
                    bottom_texture_status=texture_status,
                )
            else:
                record.update(
                    apparent_bottom_texture_scene_share=(
                        float(
                            valid.apparent_bottom_texture_candidate.fillna(False)
                            .astype(bool)
                            .mean()
                        )
                        if "apparent_bottom_texture_candidate" in valid
                        else np.nan
                    ),
                    bottom_visibility_proxy_share=np.nan,
                    apparent_bottom_texture_persistence=np.nan,
                    bottom_texture_status="insufficient_repeatability_not_verified",
                )
            for name in mask_columns:
                record[name] = float(pd.to_numeric(group[name], errors="coerce").mean())
            for output, source in (
                ("cloud_exclusion_p50", "cloud_excluded_share"),
                ("shadow_exclusion_p50", "shadow_excluded_share"),
                ("glint_exclusion_p50", "glint_excluded_share"),
                ("land_exclusion_p50", "land_excluded_share"),
                ("whitewater_exclusion_p50", "whitewater_excluded_share"),
            ):
                record[output] = (
                    float(pd.to_numeric(group[source], errors="coerce").median())
                    if source in group
                    else np.nan
                )
            records.append(record)
    return (
        pd.DataFrame.from_records(records)
        .sort_values(["segment_id", "zone_type", "period_id"])
        .reset_index(drop=True)
    )


def headline_features(
    seasonal: pd.DataFrame,
    *,
    headline_period: str = "extended_summer_may_sep",
    headline_zone: str = "nearshore",
) -> pd.DataFrame:
    chosen = seasonal.loc[
        (seasonal.period_id == headline_period) & (seasonal.zone_type == headline_zone)
    ].copy()
    if chosen.segment_id.duplicated().any():
        raise ValueError("Headline optical features are not one-to-one by segment_id")
    chosen = chosen.drop(
        columns=["zone_type", "zone_class", "period_id", "period_label", "configured_months"]
    ).reset_index(drop=True)
    summer = seasonal.loc[
        (seasonal.period_id == "summer_jja") & (seasonal.zone_type == headline_zone),
        ["segment_id", "clarity_percentile_p50"],
    ].rename(columns={"clarity_percentile_p50": "clarity_summer_percentile"})
    chosen = chosen.merge(summer, on="segment_id", how="left", validate="one_to_one")
    aliases = {
        "clarity_valid_scene_count": "valid_scene_count",
        "clarity_valid_year_count": "valid_year_count",
        "clarity_valid_month_count": "valid_month_count",
        "clarity_median_percentile": "clarity_percentile_p50",
        "clarity_p90_percentile": "clarity_percentile_p90",
        "clarity_primary_limitations": "clarity_limitation_reasons",
    }
    for alias, source in aliases.items():
        chosen[alias] = chosen[source]
    return chosen


def best_month(seasonal: pd.DataFrame) -> pd.DataFrame:
    monthly = seasonal.loc[seasonal.period_id.isin({"may", "june", "july", "august", "september"})]
    records: list[dict[str, object]] = []
    for segment_id, group in monthly.groupby("segment_id", sort=True):
        usable = group.dropna(subset=["clarity_percentile_p50"])
        if usable.empty:
            records.append(
                {
                    "segment_id": segment_id,
                    "best_month": None,
                    "most_reliable_month": None,
                    "clarity_best_month": None,
                    "clarity_most_reliable_month": None,
                }
            )
            continue
        best = usable.sort_values(
            ["clarity_percentile_p50", "period_id"], ascending=[False, True]
        ).iloc[0]
        reliable = usable.sort_values(
            ["valid_scene_count", "period_id"], ascending=[False, True]
        ).iloc[0]
        records.append(
            {
                "segment_id": segment_id,
                "best_month": str(best.period_id),
                "most_reliable_month": str(reliable.period_id),
                "clarity_best_month": str(best.period_id),
                "clarity_most_reliable_month": str(reliable.period_id),
            }
        )
    return pd.DataFrame.from_records(records)
