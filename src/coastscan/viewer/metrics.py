"""Central metadata registry for every map-display metric."""

from collections.abc import Iterable

from coastscan.viewer.models import MetricDefinition

TERRAIN_NOTICE = (
    "This describes terrestrial morphology only and does not provide underwater information."
)
BATHYMETRY_NOTICE = (
    "This is a regional bathymetry proxy; it does not measure water beneath an individual coastal "
    "ledge or resolve submerged obstacles."
)
QUALITY_NOTICE = "This is a data-quality indicator, not a site-level judgement."
OPTICAL_NOTICE = (
    "Historical region-relative optical screening only; this does not measure current conditions, "
    "physical visibility depth, underwater clearance, suitability or safety."
)


def _continuous(
    field: str,
    name: str,
    category: str,
    unit: str,
    description: str,
    *,
    scale: str = "sequential",
    value_format: str = ".2f",
) -> MetricDefinition:
    return MetricDefinition(
        field_name=field,
        display_name=name,
        category=category,  # type: ignore[arg-type]
        unit=unit,
        description=description,
        higher_is_not_necessarily_better=True,
        value_format=value_format,
        recommended_scale=scale,  # type: ignore[arg-type]
        missing_value_text="Not available",
        safety_interpretation=(
            TERRAIN_NOTICE
            if category == "terrain"
            else OPTICAL_NOTICE
            if category == "optical"
            else BATHYMETRY_NOTICE
        ),
    )


def _categorical(
    field: str,
    name: str,
    description: str,
    *,
    boolean: bool = False,
    category: str = "quality",
) -> MetricDefinition:
    return MetricDefinition(
        field_name=field,
        display_name=name,
        category=category,  # type: ignore[arg-type]
        unit="",
        description=description,
        higher_is_not_necessarily_better=True,
        value_format="s",
        recommended_scale="categorical",
        missing_value_text="Not recorded",
        safety_interpretation=OPTICAL_NOTICE if category == "optical" else QUALITY_NOTICE,
        kind="boolean" if boolean else "categorical",
    )


_DEFINITIONS = [
    _continuous(
        "land_relief_25m_p50_m",
        "Median land relief within 25 m",
        "terrain",
        "m",
        "Median rise from the terrain origin within 25 m inland.",
    ),
    _continuous(
        "land_relief_50m_p50_m",
        "Median land relief within 50 m",
        "terrain",
        "m",
        "Median rise from the terrain origin within 50 m inland.",
    ),
    _continuous(
        "land_relief_100m_p50_m",
        "Median land relief within 100 m",
        "terrain",
        "m",
        "Median rise from the terrain origin within 100 m inland.",
    ),
    _continuous(
        "land_relief_50m_p90_m",
        "Land relief p90 within 50 m",
        "terrain",
        "m",
        "Ninetieth-percentile relief across terrain transects at 50 m inland.",
    ),
    _continuous(
        "land_relief_100m_p90_m",
        "Land relief p90 within 100 m",
        "terrain",
        "m",
        "Ninetieth-percentile relief across terrain transects at 100 m inland.",
    ),
    _continuous(
        "slope_p50_deg",
        "Median terrestrial slope",
        "terrain",
        "°",
        "Median terrain slope sampled along inland transects.",
    ),
    _continuous(
        "slope_p90_deg",
        "Terrestrial slope p90",
        "terrain",
        "°",
        "Ninetieth-percentile terrain slope sampled along inland transects.",
    ),
    _continuous(
        "slope_max_deg",
        "Maximum sampled terrestrial slope",
        "terrain",
        "°",
        "Maximum valid terrain slope sampled for the segment.",
    ),
    _continuous(
        "steep_sample_share",
        "Steep terrain sample share",
        "terrain",
        "share",
        "Share of valid terrain samples above the configured steep-slope threshold.",
    ),
    _continuous(
        "steep_nearshore_transect_share",
        "Steep near-coast transect share",
        "terrain",
        "share",
        "Share of terrain transects with steep terrain close to the coast.",
    ),
    _continuous(
        "distance_to_first_steep_sample_p50_m",
        "Median distance to first steep terrain",
        "terrain",
        "m",
        "Median inland distance to the first steep terrain sample.",
    ),
    _continuous(
        "roughness_p90",
        "Terrain roughness p90",
        "terrain",
        "m",
        "Ninetieth-percentile local elevation variability.",
    ),
    _continuous(
        "terrain_valid_sample_share",
        "Terrain valid-sample share",
        "terrain",
        "share",
        "Share of requested terrain samples containing valid DEM values.",
    ),
    _continuous(
        "bathymetry_first_valid_distance_p50_m",
        "Median first-valid bathymetry distance",
        "bathymetry",
        "m",
        "Median distance from the coastline to the first valid marine bathymetry cell.",
    ),
    _continuous(
        "bathymetry_first_valid_distance_p90_m",
        "First-valid bathymetry distance p90",
        "bathymetry",
        "m",
        "Ninetieth-percentile coastline-to-first-valid-bathymetry distance.",
    ),
    _continuous(
        "depth_100m_p50_m",
        "Median depth proxy 100 m offshore",
        "bathymetry",
        "m",
        "Median regional bathymetry depth approximately 100 m offshore; this may be below "
        "native resolution.",
    ),
    _continuous(
        "depth_250m_p50_m",
        "Median depth proxy 250 m offshore",
        "bathymetry",
        "m",
        "Median regional bathymetry depth approximately 250 m offshore.",
    ),
    _continuous(
        "depth_500m_p50_m",
        "Median depth proxy 500 m offshore",
        "bathymetry",
        "m",
        "Median regional bathymetry depth approximately 500 m offshore.",
    ),
    _continuous(
        "depth_1000m_p50_m",
        "Median depth proxy 1,000 m offshore",
        "bathymetry",
        "m",
        "Median regional bathymetry depth approximately 1,000 m offshore.",
    ),
    _continuous(
        "gradient_100_500m_p50",
        "Regional gradient proxy 100–500 m",
        "bathymetry",
        "ratio",
        "Median positive-down depth change per horizontal metre between 100 and 500 m.",
        scale="diverging",
        value_format=".4f",
    ),
    _continuous(
        "gradient_250_1000m_p50",
        "Regional gradient proxy 250–1,000 m",
        "bathymetry",
        "ratio",
        "Median positive-down depth change per horizontal metre between 250 and 1,000 m.",
        scale="diverging",
        value_format=".4f",
    ),
    _continuous(
        "distance_to_5m_depth_p50_m",
        "Approximate distance to 5 m depth",
        "bathymetry",
        "m",
        "Median approximate offshore distance where the regional grid first reaches 5 m depth.",
    ),
    _continuous(
        "distance_to_10m_depth_p50_m",
        "Approximate distance to 10 m depth",
        "bathymetry",
        "m",
        "Median approximate offshore distance where the regional grid first reaches 10 m depth.",
    ),
    _continuous(
        "distance_to_20m_depth_p50_m",
        "Approximate distance to 20 m depth",
        "bathymetry",
        "m",
        "Median approximate offshore distance where the regional grid first reaches 20 m depth.",
    ),
    _continuous(
        "distance_to_30m_depth_p50_m",
        "Approximate distance to 30 m depth",
        "bathymetry",
        "m",
        "Median approximate offshore distance where the regional grid first reaches 30 m depth.",
    ),
    _continuous(
        "bathymetry_valid_transect_share",
        "Bathymetry valid-transect share",
        "bathymetry",
        "share",
        "Share of dedicated bathymetry transects with a valid marine origin.",
    ),
    _continuous(
        "interpolated_cell_share",
        "Interpolated-cell share",
        "bathymetry",
        "share",
        "Share of sampled cells marked interpolated or extrapolated by the source grid.",
    ),
    _continuous(
        "extrapolated_cell_share",
        "Extrapolated-cell share",
        "bathymetry",
        "share",
        "Share of sampled cells explicitly identified as extrapolated where the source "
        "distinguishes this.",
    ),
    _continuous(
        "global_fallback_source_share",
        "Global-fallback source share",
        "bathymetry",
        "share",
        "Share of sampled cells attributed to a global fallback source where an official lookup "
        "is available.",
    ),
    _continuous(
        "survey_source_share",
        "Survey source share",
        "bathymetry",
        "share",
        "Share of sampled cells attributed to survey sources where an official lookup is "
        "available.",
    ),
    _continuous(
        "clarity_percentile_p50",
        "Median relative clarity percentile",
        "optical",
        "percentile",
        "Median within-scene, regionally normalised historical clarity signal for the selected "
        "period.",
    ),
    _continuous(
        "clarity_percentile_p90",
        "Relative clarity percentile p90",
        "optical",
        "percentile",
        "Upper-decile historical region-relative clarity signal for the selected period.",
    ),
    _continuous(
        "clear_water_observation_share",
        "Clear-looking observation share",
        "optical",
        "share",
        "Share of valid observations above the configured regional clear-percentile threshold.",
    ),
    _continuous(
        "turbid_water_observation_share",
        "Turbid-looking observation share",
        "optical",
        "share",
        "Share of valid observations below the configured regional turbidity-proxy threshold.",
    ),
    _continuous(
        "clarity_persistence",
        "Historical clarity persistence",
        "optical",
        "share",
        "Persistence of relatively clear-looking valid observations; not a current-condition "
        "claim.",
    ),
    _continuous(
        "clarity_variability_iqr",
        "Clarity variability IQR",
        "optical",
        "percentile points",
        "Interquartile range of region-relative clarity observations.",
    ),
    _continuous(
        "valid_scene_count",
        "Valid optical scene count",
        "optical",
        "scenes",
        "Distinct official Sentinel-2 scenes supporting the selected period.",
        value_format=".0f",
    ),
    _continuous(
        "valid_observation_share",
        "Valid optical observation share",
        "optical",
        "share",
        "Share of candidate observations remaining after optical exclusions.",
    ),
    _continuous(
        "bottom_visibility_proxy_share",
        "Apparent bottom-texture observation share",
        "optical",
        "share",
        "Share of suitable observations with a bottom-like texture proxy; no depth is inferred.",
    ),
    _continuous(
        "apparent_bottom_texture_persistence",
        "Apparent bottom-texture persistence",
        "optical",
        "share",
        "Cross-scene repeatability of apparent texture after quality gating.",
    ),
    _categorical(
        "clarity_data_confidence",
        "Optical data confidence",
        "Evidence sufficiency for the selected optical period, separate from clarity.",
        category="optical",
    ),
    _categorical(
        "clarity_quality_flag",
        "Optical quality",
        "Machine-readable quality class for the selected optical period.",
        category="optical",
    ),
    _categorical(
        "orientation_status", "Orientation status", "Phase 1 landward/seaward orientation result."
    ),
    _categorical(
        "terrain_quality_flag",
        "Terrain quality",
        "Phase 1 terrain completeness and quality classification.",
    ),
    _categorical(
        "bathymetry_quality_flag", "Bathymetry quality", "Phase 2 coverage and usability flag."
    ),
    _categorical(
        "bathymetry_screening_class",
        "Bathymetry screening class",
        "Transparent regional bathymetry usability classification.",
    ),
    _categorical(
        "orientation_source_mismatch_flag",
        "Coastline/source mismatch flag",
        "Whether the official coastline and orientation-mask boundary differ beyond tolerance.",
        boolean=True,
    ),
]

METRIC_REGISTRY: dict[str, MetricDefinition] = {
    definition.field_name: definition for definition in _DEFINITIONS
}


def metric_definition(field_name: str) -> MetricDefinition | None:
    return METRIC_REGISTRY.get(field_name)


def available_metrics(
    columns: Iterable[object], *, include_bathymetry: bool = True
) -> list[MetricDefinition]:
    available = {str(column) for column in columns}
    return [
        definition
        for definition in _DEFINITIONS
        if definition.field_name in available
        and (include_bathymetry or definition.category != "bathymetry")
    ]


def grouped_metric_options(
    columns: Iterable[object], *, include_bathymetry: bool = True
) -> dict[str, list[MetricDefinition]]:
    groups: dict[str, list[MetricDefinition]] = {
        "Terrain": [],
        "Bathymetry": [],
        "Optical": [],
        "Quality": [],
    }
    for definition in available_metrics(columns, include_bathymetry=include_bathymetry):
        key = definition.category.title()
        groups[key].append(definition)
    return {key: value for key, value in groups.items() if value}


def validate_registry() -> list[str]:
    errors: list[str] = []
    for field_name, definition in METRIC_REGISTRY.items():
        if field_name != definition.field_name:
            errors.append(f"registry key mismatch: {field_name}")
        if not definition.display_name or not definition.description:
            errors.append(f"missing display metadata: {field_name}")
        if definition.unit is None or not definition.missing_value_text:
            errors.append(f"missing unit/value metadata: {field_name}")
        if not definition.safety_interpretation:
            errors.append(f"missing interpretation boundary: {field_name}")
    return errors
