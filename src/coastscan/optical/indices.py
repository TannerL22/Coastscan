"""Relative coastal-water clarity components and within-scene normalisation."""

import numpy as np
import pandas as pd
from numpy.typing import NDArray


def safe_ratio(
    numerator: NDArray[np.floating], denominator: NDArray[np.floating]
) -> NDArray[np.float64]:
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.asarray(numerator, dtype=float) / np.asarray(denominator, dtype=float)
    result[~np.isfinite(result)] = np.nan
    return np.asarray(result, dtype=np.float64)


def blue_green_ratio(
    blue: NDArray[np.floating], green: NDArray[np.floating]
) -> NDArray[np.float64]:
    return safe_ratio(blue, green)


def ndti(red: NDArray[np.floating], green: NDArray[np.floating]) -> NDArray[np.float64]:
    return safe_ratio(red - green, red + green)


def summarise_components(
    blue: NDArray[np.floating],
    green: NDArray[np.floating],
    red: NDArray[np.floating],
    nir: NDArray[np.floating],
    valid: NDArray[np.bool_],
) -> dict[str, float]:
    def median(values: NDArray[np.floating]) -> float:
        selected = values[valid]
        return float(np.nanmedian(selected)) if selected.size else float("nan")

    return {
        "blue_green_ratio": median(blue_green_ratio(blue, green)),
        "ndti": median(ndti(red, green)),
        "nir_reflectance": median(nir),
    }


def regional_percentiles(
    frame: pd.DataFrame,
    *,
    scene_field: str = "scene_id",
    group_fields: tuple[str, ...] = ("zone_type",),
    minimum_population: int = 5,
) -> pd.DataFrame:
    """Direction-aware percentile ranks; constant valid groups receive a neutral 50."""
    result = frame.copy()
    directions = {"blue_green_ratio": 1.0, "ndti": -1.0, "nir_reflectance": -1.0}
    percentile_fields: list[str] = []
    keys = [scene_field, *group_fields]
    for component, direction in directions.items():
        output = f"{component}_clarity_percentile"
        percentile_fields.append(output)
        result[output] = np.nan
        if component not in result:
            continue
        for _, indices in result.groupby(keys, dropna=False, sort=True).groups.items():
            values = pd.to_numeric(result.loc[indices, component], errors="coerce")
            valid = values.dropna()
            if len(valid) < minimum_population:
                continue
            if valid.nunique() == 1:
                ranked = pd.Series(50.0, index=valid.index)
            else:
                ranked = valid.rank(method="average", pct=True) * 100.0
                if direction < 0:
                    ranked = 100.0 - ranked + 100.0 / len(valid)
            result.loc[ranked.index, output] = ranked
    result["valid_clarity_component_count"] = result[percentile_fields].notna().sum(axis=1)
    result["clarity_percentile"] = result[percentile_fields].mean(axis=1, skipna=True)
    result.loc[result.valid_clarity_component_count == 0, "clarity_percentile"] = np.nan
    return result
