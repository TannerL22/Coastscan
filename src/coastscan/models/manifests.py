"""Run manifest model."""

from typing import Any

from pydantic import BaseModel, Field


class RunManifest(BaseModel):
    run_id: str
    region_id: str
    pipeline_version: str
    git_commit: str | None
    started_at_utc: str
    completed_at_utc: str | None = None
    status: str
    configuration_path: str
    configuration_checksum: str
    input_files: list[str] = Field(default_factory=list)
    input_checksums: dict[str, str] = Field(default_factory=dict)
    input_crs: dict[str, str] = Field(default_factory=dict)
    input_resolutions: dict[str, list[float]] = Field(default_factory=dict)
    output_files: list[str] = Field(default_factory=list)
    output_checksums: dict[str, str] = Field(default_factory=dict)
    feature_counts: dict[str, int] = Field(default_factory=dict)
    warning_counts: dict[str, int] = Field(default_factory=dict)
    quality_results: dict[str, Any] = Field(default_factory=dict)
    software_versions: dict[str, str] = Field(default_factory=dict)
