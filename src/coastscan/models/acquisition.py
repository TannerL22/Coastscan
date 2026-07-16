"""Validated authoritative acquisition manifests."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class AcquisitionResource(BaseModel):
    resource_name: str = Field(min_length=1)
    method: Literal[
        "cnig_catalogue", "direct_http", "ogc_wcs", "emodnet_product", "manual_request"
    ] = "cnig_catalogue"
    cnig_sequential_id: str | None = None
    url: str | None = None
    manual_instructions: str | None = None
    local_relative_path: Path
    expected_checksum: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    archive: bool = False
    extract_to: Path | None = None
    download_status: str = "pending"
    retrieved_at_utc: str | None = None
    response_metadata: dict[str, str] = Field(default_factory=dict)

    def model_post_init(self, __context: object) -> None:
        if self.method == "cnig_catalogue" and not self.cnig_sequential_id:
            raise ValueError("cnig_catalogue resources require cnig_sequential_id")
        if self.method in {"direct_http", "ogc_wcs", "emodnet_product"} and not self.url:
            raise ValueError(f"{self.method} resources require url")
        if self.method == "manual_request" and not self.manual_instructions:
            raise ValueError("manual_request resources require manual_instructions")


class AcquisitionSource(BaseModel):
    source_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    product_version: str = Field(min_length=1)
    edition_date: str = Field(min_length=1)
    official_product_reference: str = Field(min_length=1)
    licence: str = Field(min_length=1)
    required_attribution: str = Field(min_length=1)
    remote_metadata: dict[str, object] = Field(default_factory=dict)
    resources: list[AcquisitionResource] = Field(min_length=1)


class RegionAcquisitionManifest(BaseModel):
    region_id: str = Field(min_length=1)
    retrieved_at_utc: str | None = None
    sources: list[AcquisitionSource] = Field(min_length=1)
    download_status: str = "pending"
    manual_action_required: bool = False
    notes: list[str] = Field(default_factory=list)
