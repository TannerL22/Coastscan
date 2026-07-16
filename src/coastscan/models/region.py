"""Pydantic models for region configuration."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyproj import CRS


class StrictModel(BaseModel):
    """Reject misspelled configuration keys."""

    model_config = ConfigDict(extra="forbid")


class VectorInput(StrictModel):
    path: Path
    layer: str | None = None
    source_id: str

    @field_validator("path")
    @classmethod
    def supported_vector(cls, value: Path) -> Path:
        if value.suffix.lower() not in {".gpkg", ".geojson", ".json", ".shp", ".parquet"}:
            raise ValueError("must be a GDAL-readable polygon file (.gpkg/.geojson/.shp/.parquet)")
        return value


class RasterInput(StrictModel):
    path: Path
    source_id: str
    vertical_units: str = "metres"


class InputsConfig(StrictModel):
    land_polygon: VectorInput
    elevation: RasterInput


class CoastlineConfig(StrictModel):
    target_segment_length_m: float = Field(gt=0)
    minimum_segment_length_m: float = Field(gt=0)
    simplification_tolerance_m: float = Field(ge=0)
    orientation_test_distance_m: float = Field(gt=0)
    orientation_fallback_distances_m: list[float] = Field(default_factory=list)
    include_interior_shorelines: bool = False

    @model_validator(mode="after")
    def segment_lengths(self) -> "CoastlineConfig":
        if self.minimum_segment_length_m > self.target_segment_length_m:
            raise ValueError("minimum_segment_length_m cannot exceed target_segment_length_m")
        if any(distance <= 0 for distance in self.orientation_fallback_distances_m):
            raise ValueError("orientation fallback distances must be positive")
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
