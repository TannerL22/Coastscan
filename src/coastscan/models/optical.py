"""Typed, optional Phase 3 optical configuration."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OpticalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpticalAssets(OpticalModel):
    blue: str
    green: str
    red: str
    nir: str
    swir1: str
    scene_classification: str
    product_metadata: str = "product_metadata"
    granule_metadata: str = "granule_metadata"

    @field_validator("*")
    @classmethod
    def nonempty_asset(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("asset keys cannot be empty")
        return value


class OpticalInput(OpticalModel):
    source_id: str
    provider_adapter: Literal["copernicus_stac"]
    collection: str
    authentication: Literal["copernicus_s3"]
    catalogue_endpoint: str = "https://stac.dataspace.copernicus.eu/v1"
    s3_endpoint: str = "https://eodata.dataspace.copernicus.eu"
    required_assets: OpticalAssets


class OpticalCatalogueConfig(OpticalModel):
    maximum_catalogue_cloud_cover_percent: float = Field(ge=0, le=100)
    maximum_items: int = Field(gt=0, le=5000)
    deduplicate_same_datetime: bool = True


class OpticalZoneConfig(OpticalModel):
    nearshore_inner_m: float = Field(ge=0)
    nearshore_outer_m: float = Field(gt=0)
    coastal_inner_m: float = Field(ge=0)
    coastal_outer_m: float = Field(gt=0)
    context_inner_m: float = Field(ge=0)
    context_outer_m: float = Field(gt=0)
    along_coast_origin_spacing_m: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered_nonoverlapping(self) -> "OpticalZoneConfig":
        ranges = [
            (self.nearshore_inner_m, self.nearshore_outer_m),
            (self.coastal_inner_m, self.coastal_outer_m),
            (self.context_inner_m, self.context_outer_m),
        ]
        if any(inner >= outer for inner, outer in ranges):
            raise ValueError("every optical zone outer distance must exceed its inner distance")
        if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:], strict=False)):
            raise ValueError("optical zone distances must be ordered and non-overlapping")
        return self


class OpticalMaskConfig(OpticalModel):
    minimum_land_exclusion_m: float = Field(ge=0)
    minimum_valid_water_pixels_per_zone: int = Field(gt=0)
    minimum_valid_zone_pixel_share: float = Field(gt=0, le=1)
    maximum_cloud_excluded_share: float = Field(default=0.5, ge=0, le=1)
    maximum_shadow_excluded_share: float = Field(default=0.35, ge=0, le=1)
    maximum_glint_excluded_share: float = Field(default=0.35, ge=0, le=1)
    maximum_land_excluded_share: float = Field(default=0.5, ge=0, le=1)
    maximum_whitewater_excluded_share: float = Field(default=0.35, ge=0, le=1)
    enable_glint_risk_mask: bool = True
    enable_whitewater_mask: bool = True
    enable_dark_shadow_mask: bool = True


class OpticalClarityConfig(OpticalModel):
    clear_percentile_threshold: float = Field(ge=0, le=100)
    turbid_percentile_threshold: float = Field(ge=0, le=100)
    minimum_valid_components: int = Field(gt=0, le=6)
    minimum_valid_scenes: int = Field(gt=0)
    minimum_valid_months: int = Field(gt=0, le=12)
    minimum_scenes_per_month: int = Field(gt=0)
    minimum_regional_population: int = Field(default=5, gt=1)

    @model_validator(mode="after")
    def percentile_order(self) -> "OpticalClarityConfig":
        if self.clear_percentile_threshold <= self.turbid_percentile_threshold:
            raise ValueError("clear percentile must exceed turbid percentile")
        return self


class BottomTextureConfig(OpticalModel):
    enabled: bool = True
    minimum_valid_scenes: int = Field(gt=0)
    minimum_cross_scene_persistence: float = Field(ge=0, le=1)


class OpticalConfig(OpticalModel):
    historical_start: date
    historical_end: date
    months: list[int]
    include_partial_current_year: bool = False
    partial_year_label: str = "incomplete"
    periods: dict[str, list[int]] = Field(default_factory=dict)
    catalogue: OpticalCatalogueConfig
    zones: OpticalZoneConfig
    masks: OpticalMaskConfig
    clarity: OpticalClarityConfig
    bottom_texture: BottomTextureConfig
    maximum_optical_cache_gb: float = Field(default=8.0, gt=0)
    write_observations: bool = True

    @field_validator("months")
    @classmethod
    def valid_months(cls, value: list[int]) -> list[int]:
        if not value or any(month < 1 or month > 12 for month in value):
            raise ValueError("months must contain values from 1 through 12")
        if value != sorted(set(value)):
            raise ValueError("months must be sorted with no duplicates")
        return value

    @field_validator("periods")
    @classmethod
    def valid_periods(cls, value: dict[str, list[int]]) -> dict[str, list[int]]:
        for identifier, months in value.items():
            if not identifier or not months or any(month < 1 or month > 12 for month in months):
                raise ValueError("period definitions require an ID and valid months")
        return value

    @model_validator(mode="after")
    def ordered_dates(self) -> "OpticalConfig":
        if self.historical_start > self.historical_end:
            raise ValueError("historical_start must not follow historical_end")
        return self
