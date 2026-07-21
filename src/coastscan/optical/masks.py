"""Transparent optical exclusion masks and burden accounting."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

SCL_CLOUD = frozenset({8, 9})
SCL_SHADOW = frozenset({2, 3})
SCL_CIRRUS = frozenset({10})
SCL_LAND_OR_INVALID = frozenset({0, 1, 4, 5, 11})


@dataclass(frozen=True)
class OpticalMasks:
    spectral_water: NDArray[np.bool_]
    cloud: NDArray[np.bool_]
    shadow: NDArray[np.bool_]
    cirrus: NDArray[np.bool_]
    land: NDArray[np.bool_]
    dark_shadow: NDArray[np.bool_]
    whitewater: NDArray[np.bool_]
    glint_risk: NDArray[np.bool_]
    invalid_input: NDArray[np.bool_]
    valid_water: NDArray[np.bool_]

    def shares(self, zone: NDArray[np.bool_] | None = None) -> dict[str, float]:
        selected = np.ones(self.valid_water.shape, dtype=bool) if zone is None else zone
        denominator = int(selected.sum())
        names = (
            "spectral_water",
            "cloud",
            "shadow",
            "cirrus",
            "land",
            "dark_shadow",
            "whitewater",
            "glint_risk",
            "invalid_input",
            "valid_water",
        )
        if denominator == 0:
            return {f"{name}_share": 0.0 for name in names}
        return {
            f"{name}_share": float((getattr(self, name) & selected).sum() / denominator)
            for name in names
        }

    def shares_at(self, indices: NDArray[np.integer]) -> dict[str, float]:
        """Return mask shares for sparse flat zone indices without allocating a full grid mask."""
        names = (
            "spectral_water",
            "cloud",
            "shadow",
            "cirrus",
            "land",
            "dark_shadow",
            "whitewater",
            "glint_risk",
            "invalid_input",
            "valid_water",
        )
        if not len(indices):
            return {f"{name}_share": 0.0 for name in names}
        return {
            f"{name}_share": float(np.mean(getattr(self, name).ravel()[indices])) for name in names
        }


def _finite(*bands: NDArray[np.floating]) -> NDArray[np.bool_]:
    result = np.ones(bands[0].shape, dtype=bool)
    for band in bands:
        result &= np.isfinite(band)
    return result


def build_masks(
    blue: NDArray[np.floating],
    green: NDArray[np.floating],
    red: NDArray[np.floating],
    nir: NDArray[np.floating],
    swir1: NDArray[np.floating],
    scl: NDArray[np.integer],
    *,
    vector_land: NDArray[np.bool_] | None = None,
) -> OpticalMasks:
    """Create conservative masks; thresholds are dimensionless reflectance heuristics."""
    finite = _finite(blue, green, red, nir, swir1)
    cloud = np.isin(scl, list(SCL_CLOUD))
    shadow = np.isin(scl, list(SCL_SHADOW))
    cirrus = np.isin(scl, list(SCL_CIRRUS))
    land = np.isin(scl, list(SCL_LAND_OR_INVALID))
    if vector_land is not None:
        land |= vector_land
    invalid_input = ~finite
    water_like = (green > nir * 1.05) & (green > swir1 * 1.15)
    scl_usable_water = water_like & ~(cloud | shadow | cirrus | land | invalid_input)
    dark_shadow = scl_usable_water & (blue + green + red < 0.045)
    whiteness = np.maximum.reduce([blue, green, red]) - np.minimum.reduce([blue, green, red])
    whitewater = scl_usable_water & (green > 0.12) & (whiteness < 0.035) & (nir > 0.04)
    glint_risk = (
        scl_usable_water
        & (green > swir1)
        & (nir > 0.06)
        & (swir1 > 0.025)
        & (nir / (green + 1e-6) > 0.45)
    )
    excluded = (
        cloud | shadow | cirrus | land | dark_shadow | whitewater | glint_risk | invalid_input
    )
    valid_water = water_like & ~excluded
    return OpticalMasks(
        water_like,
        cloud,
        shadow,
        cirrus,
        land,
        dark_shadow,
        whitewater,
        glint_risk,
        invalid_input,
        valid_water,
    )


def validity_reason(masks: OpticalMasks, zone: NDArray[np.bool_], minimum_pixels: int) -> str:
    return validity_reason_at(masks, np.flatnonzero(zone), minimum_pixels)


def validity_reason_at(
    masks: OpticalMasks, indices: NDArray[np.integer], minimum_pixels: int
) -> str:
    if not len(indices):
        return "empty_zone"
    valid_count = int(masks.valid_water.ravel()[indices].sum())
    if valid_count < minimum_pixels:
        shares = masks.shares_at(indices)
        exclusions = {
            "cloud": shares["cloud_share"],
            "shadow": shares["shadow_share"],
            "cirrus": shares["cirrus_share"],
            "land": shares["land_share"],
            "dark_shadow": shares["dark_shadow_share"],
            "whitewater": shares["whitewater_share"],
            "glint_risk": shares["glint_risk_share"],
            "invalid_input": shares["invalid_input_share"],
            "non_spectral_water": 1.0 - shares["spectral_water_share"],
        }
        dominant = max(exclusions, key=exclusions.__getitem__)
        return f"insufficient_valid_pixels:{dominant}"
    return "valid"
