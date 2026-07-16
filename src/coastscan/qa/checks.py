"""Automated geometry, segmentation, orientation, and terrain checks."""

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon


def _result(passed: bool, value: Any, threshold: str) -> dict[str, Any]:
    return {"passed": bool(passed), "value": value, "threshold": threshold}


def run_qa_checks(
    land: Polygon | MultiPolygon,
    coastline: gpd.GeoDataFrame,
    segments: gpd.GeoDataFrame,
    transects: gpd.GeoDataFrame,
    features: pd.DataFrame,
    *,
    target_segment_length_m: float,
    minimum_segment_length_m: float,
    maximum_ambiguous_share: float,
    maximum_missing_terrain_share: float,
) -> dict[str, Any]:
    """Return every check and an overall pass flag without hiding failures."""
    checks: dict[str, dict[str, Any]] = {}
    checks["land_valid"] = _result(
        land.is_valid and not land.is_empty, land.geom_type, "valid non-empty"
    )
    checks["coastline_valid"] = _result(
        bool(coastline.geometry.is_valid.all() and (~coastline.geometry.is_empty).all()),
        len(coastline),
        "all valid and non-empty",
    )
    lengths = segments.segment_length_m
    checks["segments_nonempty_positive"] = _result(
        bool((~segments.geometry.is_empty).all() and (lengths > 0).all()),
        len(segments),
        "all positive",
    )
    checks["segment_ids_unique"] = _result(
        not segments.segment_id.duplicated().any(),
        int(segments.segment_id.nunique()),
        "no duplicates",
    )
    part_lengths = segments.groupby("coastline_part_id").segment_length_m.transform("sum")
    lower_ok = bool(
        (
            (lengths >= minimum_segment_length_m - 1e-6) | (part_lengths < minimum_segment_length_m)
        ).all()
    )
    upper_ok = bool((lengths <= target_segment_length_m * 1.5 + 1e-6).all())
    checks["segment_length_bounds"] = _result(
        lower_ok and upper_ok,
        {"minimum": float(lengths.min()), "maximum": float(lengths.max())},
        f">={minimum_segment_length_m:g} and <=150% target",
    )
    length_delta = abs(float(lengths.sum()) - float(coastline.length.sum()))
    checks["segmented_length_conserved"] = _result(
        length_delta <= max(0.01, float(coastline.length.sum()) * 1e-8),
        length_delta,
        "<=1e-8 relative",
    )
    ambiguous_share = float((segments.orientation_status == "ambiguous").mean())
    checks["orientation_ambiguous_share"] = _result(
        ambiguous_share <= maximum_ambiguous_share, ambiguous_share, f"<={maximum_ambiguous_share}"
    )
    checks["orientation_fallback_count"] = _result(
        True, int((segments.orientation_status == "resolved_fallback").sum()), "reported"
    )
    checks["transects_valid"] = _result(
        bool((~transects.geometry.is_empty).all() and transects.geometry.is_valid.all()),
        len(transects),
        "all valid",
    )
    if len(transects):
        origins = transects.geometry.map(lambda line: line.coords[0])
        origin_distances = [
            segments.loc[segments.segment_id == segment_id]
            .geometry.iloc[0]
            .distance(__import__("shapely").geometry.Point(origin))
            for origin, segment_id in zip(origins, transects.segment_id, strict=True)
        ]
        checks["transect_origins_on_coast"] = _result(
            max(origin_distances, default=0) <= 1e-6, max(origin_distances, default=0), "<=1e-6 m"
        )
        inland = transects.loc[transects.direction == "inland"]
        offshore = transects.loc[transects.direction == "offshore"]
        inland_share = float(
            inland.geometry.map(
                lambda line: land.covers(__import__("shapely").geometry.Point(line.coords[-1]))
            ).mean()
        )
        offshore_share = float(
            offshore.geometry.map(
                lambda line: not land.covers(__import__("shapely").geometry.Point(line.coords[-1]))
            ).mean()
        )
        checks["inland_endpoints_on_land"] = _result(inland_share >= 0.8, inland_share, ">=0.8")
        checks["offshore_endpoints_off_land"] = _result(
            offshore_share >= 0.8, offshore_share, ">=0.8"
        )
    else:
        checks["transect_origins_on_coast"] = _result(False, 0, "transects required")
    valid_share = float(features.terrain_valid_sample_share.mean()) if len(features) else 0.0
    checks["terrain_valid_sample_share"] = _result(
        1 - valid_share <= maximum_missing_terrain_share,
        valid_share,
        f"missing share <={maximum_missing_terrain_share}",
    )
    slope_columns = ["slope_p50_deg", "slope_p90_deg", "slope_max_deg"]
    slope_values = features[slope_columns].to_numpy(dtype=float)
    finite_slopes = slope_values[np.isfinite(slope_values)]
    checks["slope_range"] = _result(
        bool(
            finite_slopes.size
            and (finite_slopes >= -1e-6).all()
            and (finite_slopes <= 90.000001).all()
        ),
        {
            "minimum": float(finite_slopes.min()) if finite_slopes.size else None,
            "maximum": float(finite_slopes.max()) if finite_slopes.size else None,
        },
        "0..90 degrees",
    )
    checks["relief_finite_or_missing"] = _result(
        not np.isinf(features.filter(like="land_relief_").to_numpy(dtype=float)).any(),
        "checked",
        "no infinite values",
    )
    failed = [name for name, result in checks.items() if not result["passed"]]
    return {"passed": not failed, "failed_checks": failed, "checks": checks}
