"""Sentinel-2 radiometry and explicit categorical/continuous resampling rules."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from rasterio.enums import Resampling


@dataclass(frozen=True)
class Radiometry:
    scale: float
    offset: float
    nodata: float | int | None = None
    processing_baseline: str = "unknown"


def reflectance(digital_numbers: NDArray[np.generic], metadata: Radiometry) -> NDArray[np.float32]:
    """Convert stored values to surface reflectance while preserving invalid pixels."""
    values = digital_numbers.astype("float32")
    invalid = ~np.isfinite(values)
    if metadata.nodata is not None:
        invalid |= values == metadata.nodata
    converted = values * np.float32(metadata.scale) + np.float32(metadata.offset)
    converted[invalid] = np.nan
    return converted


def resampling_for_asset(*, categorical: bool) -> Resampling:
    return Resampling.nearest if categorical else Resampling.bilinear
