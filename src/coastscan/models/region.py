"""Pydantic models for region configuration."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyproj import CRS


class StrictModel(BaseModel):
    """Reject misspelled configuration keys."""

    model_config = ConfigDict(extra="forbid")


class VectorInput(StrictModel):
    path: Path
    layer: str | None = None
    source_id: str
    role: str | None = None
    selection_filters: list["AttributeFilter"] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def supported_vector(cls, value: Path) -> Path:
        if value.suffix.lower() not in {".gpkg", ".geojson", ".json", ".shp", ".parquet"}:
            raise ValueError("must be a GDAL-readable vector file (.gpkg/.geojson/.shp/.parquet)")
        return value


class AttributeFilter(StrictModel):
    """Explicit source-attribute selection without inferred field meanings."""

    field: str
    accepted_values: list[Any] | None = None
    starts_with: str | None = None

    @model_validator(mode="after")
    def exactly_one_operation(self) -> "AttributeFilter":
        if (self.accepted_values is None) == (self.starts_with is None):
            raise ValueError("set exactly one of accepted_values or starts_with")
        if self.accepted_values is not None and not self.accepted_values:
            raise ValueError("accepted_values cannot be empty")
        return self


class DirectCoastlineInput(StrictModel):
    mode: Literal["direct"]
    path: Path
    layer: str | None = None
    source_id: str
    feature_filters: list[AttributeFilter] = Field(default_factory=list)
    source_id_field: str | None = None
    source_class_field: str | None = None
    duplicate_tolerance_m: float = Field(default=10.0, ge=0)

    @field_validator("path")
    @classmethod
    def supported_vector(cls, value: Path) -> Path:
        return VectorInput.supported_vector(value)


class RasterInput(StrictModel):
    path: Path | None = None
    paths: list[Path] | None = None
    directory: Path | None = None
    glob: str | None = None
    source_id: str
    vertical_units: str = "metres"
    mosaic_mode: Literal["single", "vrt"] = "single"

    @model_validator(mode="after")
    def one_path_source(self) -> "RasterInput":
        configured = sum(
            value is not None for value in (self.path, self.paths, self.directory, self.glob)
        )
        if configured != 1:
            raise ValueError("configure exactly one of path, paths, directory, or glob")
        if self.paths is not None and not self.paths:
            raise ValueError("paths cannot be empty")
        if self.path is None and self.mosaic_mode != "vrt":
            raise ValueError("multi-tile elevation requires mosaic_mode: vrt")
        if not self.vertical_units.strip():
            raise ValueError("vertical_units must be explicitly documented")
        return self


class InputsConfig(StrictModel):
    coastline: DirectCoastlineInput | None = None
    land_polygon: VectorInput | None = None
    elevation: RasterInput

    @model_validator(mode="after")
    def coastline_contract(self) -> "InputsConfig":
        if self.land_polygon is None:
            mode = "direct" if self.coastline is not None else "polygon-derived"
            raise ValueError(f"{mode} coastline mode requires inputs.land_polygon")
        if self.coastline is not None and self.land_polygon.role not in {None, "orientation_mask"}:
            raise ValueError("direct mode land_polygon.role must be orientation_mask")
        return self


class AreaOfInterest(StrictModel):
    path: Path
    layer: str | None = None


class CoastlineConfig(StrictModel):
    target_segment_length_m: float = Field(gt=0)
    minimum_segment_length_m: float = Field(gt=0)
    simplification_tolerance_m: float = Field(ge=0)
    orientation_test_distance_m: float = Field(gt=0)
    orientation_fallback_distances_m: list[float] = Field(default_factory=list)
    include_interior_shorelines: bool = False
    orientation_vote_offsets_m: list[float] = Field(default_factory=lambda: [0.0])
    source_mismatch_tolerance_m: float = Field(default=5.0, ge=0)

    @model_validator(mode="after")
    def segment_lengths(self) -> "CoastlineConfig":
        if self.minimum_segment_length_m > self.target_segment_length_m:
            raise ValueError("minimum_segment_length_m cannot exceed target_segment_length_m")
        if any(distance <= 0 for distance in self.orientation_fallback_distances_m):
            raise ValueError("orientation fallback distances must be positive")
        if not self.orientation_vote_offsets_m:
            raise ValueError("orientation_vote_offsets_m cannot be empty")
        return self


class TransectConfig(StrictModel):
    spacing_m: float = Field(gt=0)
    inland_length_m: float = Field(gt=0)
    offshore_length_m: float = Field(gt=0)


class TerrainConfig(StrictModel):
    relief_distances_m: list[float]
    sample_spacing_m: float = Field(gt=0)
    steep_slope_threshold_degrees: float = Field(ge=0, le=90)
    roughness_window_m: float = Field(gt=0)
    minimum_valid_sample_share: float = Field(ge=0, le=1)
    write_samples: bool = False
    origin_search_max_distance_m: float = Field(default=20.0, ge=0)

    @field_validator("relief_distances_m")
    @classmethod
    def positive_relief_distances(cls, value: list[float]) -> list[float]:
        if not value or any(distance <= 0 for distance in value):
            raise ValueError("must contain positive distances")
        return sorted(set(value))


class QualityConfig(StrictModel):
    maximum_ambiguous_orientation_share: float = Field(ge=0, le=1)
    maximum_missing_terrain_share: float = Field(ge=0, le=1)
    random_qa_sample_size: int = Field(gt=0)


class RegionConfig(StrictModel):
    region_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    region_name: str
    country: str
    analysis_crs: str
    output_crs: str
    inputs: InputsConfig
    area_of_interest: AreaOfInterest | None = None
    coastline: CoastlineConfig
    transects: TransectConfig
    terrain: TerrainConfig
    quality: QualityConfig

    @field_validator("analysis_crs", "output_crs")
    @classmethod
    def valid_crs(cls, value: str) -> str:
        try:
            crs = CRS.from_user_input(value)
        except Exception as exc:
            raise ValueError(f"invalid CRS: {value}") from exc
        if not crs:
            raise ValueError(f"invalid CRS: {value}")
        return value

    @model_validator(mode="after")
    def projected_analysis_crs(self) -> "RegionConfig":
        crs = CRS.from_user_input(self.analysis_crs)
        if not crs.is_projected or not any(axis.unit_name == "metre" for axis in crs.axis_info):
            raise ValueError("analysis_crs must be projected with metre units")
        if max(self.terrain.relief_distances_m) > self.transects.inland_length_m:
            raise ValueError("relief distances cannot exceed inland transect length")
        return self
