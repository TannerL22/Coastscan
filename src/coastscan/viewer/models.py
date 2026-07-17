"""Typed viewer data and presentation contracts."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import geopandas as gpd

from coastscan.viewer.validation import GeometryValidationResult

MetricKind = Literal["continuous", "categorical", "boolean"]
MetricCategory = Literal["terrain", "bathymetry", "quality"]
ScaleType = Literal["sequential", "diverging", "categorical"]
ScaleMode = Literal["robust", "full"]
Availability = Literal["all", "available", "missing"]


@dataclass(frozen=True)
class MetricDefinition:
    field_name: str
    display_name: str
    category: MetricCategory
    unit: str
    description: str
    higher_is_not_necessarily_better: bool
    value_format: str
    recommended_scale: ScaleType
    missing_value_text: str
    safety_interpretation: str
    kind: MetricKind = "continuous"


@dataclass(frozen=True)
class ViewerPaths:
    region_id: str
    preferred_segments: Path
    phase1_segments: Path
    coast_segments: Path
    bathymetry_features: Path
    bathymetry_transects: Path
    manifest_directory: Path


@dataclass(frozen=True)
class ViewerData:
    region_id: str
    mode: Literal["phase2", "terrain_only"]
    analytical_segments: gpd.GeoDataFrame
    display_segments: gpd.GeoDataFrame
    paths: ViewerPaths
    source_crs: str
    segment_checksum: str
    geometry_checksum: str
    attribute_checksum: str
    geometry_source: Path
    attribute_source: Path
    geometry_validation: GeometryValidationResult
    maximum_bathymetry_transect_length_m: float = 5_000.0
    coastline_source_id: str | None = None
    manifests: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def has_bathymetry(self) -> bool:
        return self.mode == "phase2"


@dataclass(frozen=True)
class ColorScale:
    minimum: float | None
    maximum: float | None
    mode: ScaleMode
    constant: bool
    valid_count: int
    midpoint: float | None = None


@dataclass(frozen=True)
class ViewerFilters:
    orientation_statuses: frozenset[str] | None = None
    terrain_availability: Availability = "all"
    bathymetry_availability: Availability = "all"
    source_mismatch: bool | None = None
    relief_100m_range: tuple[float | None, float | None] = (None, None)
    slope_p90_range: tuple[float | None, float | None] = (None, None)
    minimum_steep_nearshore_share: float | None = None
    minimum_terrain_valid_share: float | None = None
    bathymetry_screening_classes: frozenset[str] | None = None
    minimum_bathymetry_valid_share: float | None = None
    maximum_first_valid_distance_m: float | None = None
    depth_field: str | None = None
    depth_range: tuple[float | None, float | None] = (None, None)
    gradient_field: str | None = None
    gradient_range: tuple[float | None, float | None] = (None, None)
    maximum_global_fallback_share: float | None = None
    segment_search: str = ""
