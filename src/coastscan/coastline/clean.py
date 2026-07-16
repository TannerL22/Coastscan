"""Conservative coastline cleaning with auditable length effects."""

from dataclasses import dataclass

import geopandas as gpd

from coastscan.exceptions import InvalidGeometryError


@dataclass(frozen=True)
class CleaningStats:
    length_before_m: float
    length_after_m: float
    length_change_percent: float
    geometry_count_before: int
    geometry_count_after: int
    operations: tuple[str, ...]
    warning: str | None


def clean_coastline(
    coastline: gpd.GeoDataFrame, simplification_tolerance_m: float
) -> tuple[gpd.GeoDataFrame, CleaningStats]:
    before_count = len(coastline)
    before_length = float(coastline.length.sum())
    cleaned = coastline.loc[~coastline.geometry.is_empty & (coastline.length > 0)].copy()
    operations: list[str] = ["removed_empty_or_zero_length"] if len(cleaned) != before_count else []
    if simplification_tolerance_m > 0:
        cleaned.geometry = cleaned.geometry.simplify(
            simplification_tolerance_m, preserve_topology=True
        )
        operations.append(f"topology_preserving_simplify_{simplification_tolerance_m:g}m")
    cleaned = cleaned.loc[~cleaned.geometry.is_empty & (cleaned.length > 0)].copy()
    if cleaned.empty or not cleaned.geometry.is_valid.all():
        raise InvalidGeometryError("Coastline cleaning produced empty or invalid line geometry")
    after_length = float(cleaned.length.sum())
    change = 0.0 if before_length == 0 else 100 * (after_length - before_length) / before_length
    warning = f"Coastline cleaning changed length by {change:.3f}%" if abs(change) > 1 else None
    return cleaned.reset_index(drop=True), CleaningStats(
        length_before_m=before_length,
        length_after_m=after_length,
        length_change_percent=change,
        geometry_count_before=before_count,
        geometry_count_after=len(cleaned),
        operations=tuple(operations),
        warning=warning,
    )
