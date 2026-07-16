"""Source adapters for the canonical positive-down bathymetry model."""

from typing import Literal

import numpy as np

from coastscan.exceptions import RasterValidationError

ADAPTER_VERSION = "1"


def canonicalize_depth(
    values: np.ndarray,
    sign_convention: Literal["positive_down", "negative_elevation"],
    *,
    zero_is_valid: bool,
) -> np.ndarray:
    """Convert a source array to non-negative metres positive-down without filling nodata."""
    result = values.astype("float64", copy=True)
    if sign_convention == "negative_elevation":
        result = -result
    elif sign_convention != "positive_down":
        raise RasterValidationError(f"Unsupported bathymetry sign convention: {sign_convention}")
    invalid = ~np.isfinite(result) | (result < 0)
    if not zero_is_valid:
        invalid |= result == 0
    result[invalid] = np.nan
    return result


def resolution_class(ratio: float | None, minimum_ratio: float = 1.0) -> str:
    if ratio is None or not np.isfinite(ratio):
        return "unavailable"
    if ratio < minimum_ratio:
        return "below_native_resolution"
    if ratio < 2 * minimum_ratio:
        return "marginally_resolved"
    return "well_resolved"
