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


class BathymetryRunManifest(BaseModel):
    run_id: str
    region_id: str
    pipeline_stage: str = "bathymetry_phase2"
    pipeline_version: str
    git_commit: str | None
    started_at_utc: str
    completed_at_utc: str
    status: str
    configuration_path: str
    configuration_checksum: str
    upstream_phase1_manifest: str
    upstream_segment_file: str
    upstream_segment_checksum: str
    upstream_segment_id_set_checksum: str
    bathymetry_sources: list[str]
    bathymetry_source_checksums: dict[str, str]
    source_release: str
    vertical_datum: str
    native_resolution: list[float]
    output_resolution: list[float]
    variable_mapping: dict[str, str]
    output_files: list[str]
    output_checksums: dict[str, str]
    feature_counts: dict[str, int]
    warning_counts: dict[str, int]
    quality_results: dict[str, Any]
    software_versions: dict[str, str]
    cache_key: str
    cache_used: bool
