"""Consistent value formatting, exports and deterministic interpretation text."""

import json
import math
from typing import Any, cast

import pandas as pd

from coastscan.viewer.metrics import metric_definition
from coastscan.viewer.models import MetricDefinition


def is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(cast(Any, value)))
    except (TypeError, ValueError):
        return False


def format_value(value: object, definition: MetricDefinition | None = None) -> str:
    if is_missing(value):
        return definition.missing_value_text if definition else "Not available"
    if definition is None:
        return str(value)
    if definition.kind == "boolean":
        return "Yes" if bool(value) else "No"
    if definition.kind == "categorical":
        return str(value).replace("_", " ").title()
    try:
        formatted = format(float(cast(Any, value)), definition.value_format)
    except (TypeError, ValueError):
        return str(value)
    return f"{formatted} {definition.unit}".strip()


def format_field(row: pd.Series, field_name: str) -> str:
    return format_value(row.get(field_name), metric_definition(field_name))


def screening_reasons(value: object) -> list[str]:
    if is_missing(value):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]


def deterministic_interpretation(row: pd.Series) -> str:
    """Explain transparent conditions without ranking or site-level claims."""
    sentences: list[str] = []
    relief = pd.to_numeric(pd.Series([row.get("land_relief_100m_p90_m")]), errors="coerce").iloc[0]
    terrain_share = pd.to_numeric(
        pd.Series([row.get("terrain_valid_sample_share")]), errors="coerce"
    ).iloc[0]
    if math.isfinite(terrain_share) and terrain_share > 0:
        if math.isfinite(relief) and relief >= 20:
            sentences.append("Terrestrial relief is high within 100 m of the coast.")
        elif math.isfinite(relief):
            sentences.append(
                "Terrestrial relief is comparatively modest within 100 m of the coast."
            )
        else:
            sentences.append(
                "Terrestrial coverage exists, but the 100 m relief summary is missing."
            )
    else:
        sentences.append("Terrestrial morphology is missing or incomplete for this segment.")

    bathy_share = pd.to_numeric(
        pd.Series([row.get("bathymetry_valid_transect_share")]), errors="coerce"
    ).iloc[0]
    if math.isfinite(bathy_share) and bathy_share > 0:
        gradient = pd.to_numeric(
            pd.Series([row.get("gradient_250_1000m_p50")]), errors="coerce"
        ).iloc[0]
        if math.isfinite(gradient):
            if gradient > 0.01:
                sentences.append(
                    "The regional grid shows increasing positive-down depth between 250 m and "
                    "1,000 m offshore."
                )
            elif gradient < -0.0025:
                sentences.append(
                    "The regional grid shows decreasing positive-down depth between 250 m and "
                    "1,000 m offshore."
                )
            else:
                sentences.append("The regional 250–1,000 m depth-change proxy is small or mixed.")
        screening = str(row.get("bathymetry_screening_class", "")).replace("_", "-")
        if screening:
            sentences.append(f"The bathymetry screening class is {screening}.")
        resolution = pd.to_numeric(
            pd.Series([row.get("bathymetry_native_resolution_m")]), errors="coerce"
        ).iloc[0]
        if math.isfinite(resolution):
            sentences.append(f"Native bathymetry resolution is approximately {resolution:.0f} m.")
    else:
        sentences.append("Regional bathymetry is unavailable or unresolved for this segment.")
    sentences.append(
        "No site-level underwater conclusion is supported, and submerged obstacles remain "
        "unresolved."
    )
    return " ".join(sentences)


def analytical_csv_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop geometry and clearly prefix regional proxy fields for CSV export."""
    result = pd.DataFrame(frame.drop(columns=["geometry"], errors="ignore")).copy()
    regional_prefixes = ("depth_", "gradient_", "distance_to_", "bathymetry_")
    rename = {
        column: f"regional_proxy__{column}"
        for column in result.columns
        if str(column).startswith(regional_prefixes)
    }
    return result.rename(columns=rename)


def table_columns(frame: pd.DataFrame, selected_metric: str) -> list[str]:
    preferred = [
        "segment_id",
        selected_metric,
        "orientation_status",
        "land_relief_100m_p90_m",
        "slope_p90_deg",
        "terrain_quality_flag",
        "bathymetry_first_valid_distance_p50_m",
        "depth_500m_p50_m",
        "bathymetry_valid_transect_share",
        "bathymetry_screening_class",
        "orientation_source_mismatch_flag",
    ]
    result: list[str] = []
    for field in preferred:
        if field in frame.columns and field not in result:
            result.append(field)
    return result


def safe_record(row: pd.Series, fields: list[str]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields}
