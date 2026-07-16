"""Validated authoritative acquisition manifests."""

from pathlib import Path

from pydantic import BaseModel, Field


class AcquisitionResource(BaseModel):
    resource_name: str = Field(min_length=1)
    cnig_sequential_id: str = Field(min_length=1)
    local_relative_path: Path
    expected_checksum: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    archive: bool = False
    extract_to: Path | None = None
    download_status: str = "pending"


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
