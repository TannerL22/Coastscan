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


class OpticalRunManifest(BaseModel):
    run_id: str
    region_id: str
    pipeline_stage: str = "optical_phase3"
    pipeline_version: str
    algorithm_version: str
    git_commit: str | None
    started_at_utc: str
    completed_at_utc: str
    status: str
    configuration_path: str
    configuration_checksum: str
    upstream_phase1_manifest: str | None
    upstream_phase2_manifest: str | None
    upstream_segment_file: str
    upstream_segment_checksum: str
    upstream_segment_id_set_checksum: str
    upstream_phase2_file: str
    upstream_phase2_checksum: str
    upstream_phase2_feature_checksum: str
    protected_upstream_checksums: dict[str, str]
    provider: str
    optical_provider: str
    catalogue_endpoint: str
    catalogue_endpoint_reference: str
    collection: str
    licence: str
    required_attribution: str
    historical_period: list[str]
    partial_current_period: dict[str, Any] | None = None
    selected_scene_ids: list[str]
    scene_catalogue_file: str
    scene_catalogue_checksum: str
    processing_baselines: dict[str, int]
    asset_mapping: dict[str, str]
    radiometric_method: str
    mask_method_versions: dict[str, str]
    clarity_formula: str
    bottom_texture_method: str
    zone_configuration: dict[str, Any]
    acquisition_manifest: str
    acquisition_manifest_checksum: str
    acquired_clip_count: int
    acquired_clip_bytes: int
    output_files: list[str]
    output_checksums: dict[str, str]
    feature_counts: dict[str, int]
    warning_counts: dict[str, int]
    quality_results: dict[str, Any]
    software_versions: dict[str, str]
    cache_key: str
    cache_used: bool
    secrets_recorded: bool = False
    catalogue_metadata: dict[str, Any]
