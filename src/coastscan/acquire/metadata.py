"""Region acquisition orchestration and machine-readable status persistence."""

import json
from datetime import UTC, datetime
from pathlib import Path

from coastscan.acquire.boundaries import create_documented_aoi
from coastscan.acquire.cnig import download_cnig_resource, extract_zip_safely
from coastscan.config import PROJECT_ROOT, load_region_config
from coastscan.exceptions import AcquisitionError
from coastscan.models.acquisition import RegionAcquisitionManifest


def _write_manifest(manifest: RegionAcquisitionManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def acquire_region_data(
    region: str,
    *,
    root: Path = PROJECT_ROOT,
) -> RegionAcquisitionManifest:
    """Acquire configured authoritative resources and create the documented AOI."""
    config, _ = load_region_config(region, root)
    plan_path = root / "config" / "acquisitions" / f"{config.region_id}.json"
    output_path = root / "data_catalog" / "acquisitions" / f"{config.region_id}.json"
    if not plan_path.is_file():
        raise AcquisitionError(f"No authoritative acquisition plan exists: {plan_path}")
    manifest = RegionAcquisitionManifest.model_validate_json(plan_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for source in manifest.sources:
        for resource in source.resources:
            destination = root / resource.local_relative_path
            try:
                checksum, reused = download_cnig_resource(
                    resource.cnig_sequential_id,
                    destination,
                    expected_checksum=resource.expected_checksum or resource.checksum,
                    timeout_seconds=300,
                )
                resource.checksum = checksum
                resource.size_bytes = destination.stat().st_size
                resource.download_status = "reused" if reused else "downloaded"
                if resource.archive and resource.extract_to is not None:
                    extract_zip_safely(destination, root / resource.extract_to)
            except AcquisitionError as exc:
                resource.download_status = "failed"
                errors.append(str(exc))
    if config.area_of_interest is not None:
        definition = root / "config" / "aoi" / f"{config.region_id}.json"
        create_documented_aoi(
            definition,
            root / config.area_of_interest.path,
            config.area_of_interest.layer or "aoi",
        )
    manifest.retrieved_at_utc = datetime.now(UTC).isoformat()
    manifest.download_status = "complete" if not errors else "incomplete"
    manifest.manual_action_required = bool(errors)
    manifest.notes.extend(errors)
    _write_manifest(manifest, output_path)
    if errors:
        raise AcquisitionError("; ".join(errors))
    return manifest
