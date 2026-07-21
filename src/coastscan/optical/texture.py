"""Apparent bottom-texture repeatability, kept separate from clarity."""

import numpy as np
from numpy.typing import NDArray


def texture_magnitude(image: NDArray[np.floating]) -> NDArray[np.float64]:
    values = np.asarray(image, dtype=float)
    gy, gx = np.gradient(values)
    return np.asarray(np.hypot(gx, gy), dtype=np.float64)


def texture_strength(image: NDArray[np.floating], valid: NDArray[np.bool_]) -> float:
    magnitude = texture_magnitude(image)
    selected = magnitude[valid & np.isfinite(magnitude)]
    return float(np.nanmedian(selected)) if selected.size else float("nan")


def apparent_texture_persistence(
    images: list[NDArray[np.floating]],
    valid_masks: list[NDArray[np.bool_]],
    *,
    minimum_scenes: int,
    minimum_correlation: float = 0.35,
) -> dict[str, float | int | str]:
    if len(images) < minimum_scenes or len(valid_masks) != len(images):
        return {
            "valid_scene_count": len(images),
            "persistence": float("nan"),
            "status": "insufficient",
        }
    correlations: list[float] = []
    for left in range(len(images)):
        for right in range(left + 1, len(images)):
            valid = valid_masks[left] & valid_masks[right]
            a = np.asarray(images[left], dtype=float)[valid]
            b = np.asarray(images[right], dtype=float)[valid]
            finite = np.isfinite(a) & np.isfinite(b)
            if int(finite.sum()) < 9 or np.nanstd(a[finite]) == 0 or np.nanstd(b[finite]) == 0:
                continue
            correlations.append(float(np.corrcoef(a[finite], b[finite])[0, 1]))
    if not correlations:
        return {
            "valid_scene_count": len(images),
            "persistence": float("nan"),
            "status": "insufficient",
        }
    persistence = float(np.mean(np.asarray(correlations) >= minimum_correlation))
    return {
        "valid_scene_count": len(images),
        "persistence": persistence,
        "median_cross_scene_correlation": float(np.median(correlations)),
        "status": "repeatable" if persistence >= 0.5 else "unstable",
    }
