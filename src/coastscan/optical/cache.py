"""Integrity contracts for local optical clips and cached Phase 3 outputs."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from coastscan.catalog.manifests import sha256_file
from coastscan.exceptions import AcquisitionError

CLIP_ROLES = ("blue", "green", "red", "nir", "swir1", "scl")


@dataclass(frozen=True)
class ValidatedOpticalCache:
    manifest_path: Path
    manifest_checksum: str
    file_count: int
    total_bytes: int
    file_checksums: dict[str, str]


def acquisition_manifest_path(root: Path, region_id: str) -> Path:
    return root / "data" / "interim" / region_id / "optical" / "acquisition_manifest.json"


def clip_path(root: Path, region_id: str, scene_id: str, role: str) -> Path:
    return root / "data" / "interim" / region_id / "optical" / "clips" / scene_id / f"{role}.tif"


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def read_acquisition_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AcquisitionError(
            "The optical acquisition manifest is absent. Run acquire-optical with official CDSE "
            "credentials before building clarity."
        )
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcquisitionError(f"Optical acquisition manifest is unreadable: {path}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), list):
        raise AcquisitionError(f"Optical acquisition manifest has an invalid schema: {path}")
    return parsed


def validate_acquisition_cache(
    root: Path,
    region_id: str,
    selected_scenes: pd.DataFrame,
    *,
    catalogue_checksum: str,
) -> ValidatedOpticalCache:
    """Verify exact selected scene/role coverage and every local clip checksum."""
    path = acquisition_manifest_path(root, region_id)
    manifest = read_acquisition_manifest(path)
    if manifest.get("region_id") != region_id:
        raise AcquisitionError("Optical acquisition manifest region does not match the build")
    if manifest.get("complete") is False:
        raise AcquisitionError(
            "Optical acquisition is incomplete. Re-run acquire-optical to resume verified clips."
        )
    if manifest.get("catalogue_checksum") != catalogue_checksum:
        raise AcquisitionError(
            "Optical acquisition manifest is stale relative to the selected scene catalogue. "
            "Re-run acquire-optical."
        )
    expected = {
        (str(scene_id), role)
        for scene_id in selected_scenes.scene_id.astype(str)
        for role in CLIP_ROLES
    }
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in manifest["files"]:
        if not isinstance(raw, dict):
            raise AcquisitionError("Optical acquisition manifest contains a non-object file entry")
        key = (str(raw.get("scene_id", "")), str(raw.get("asset_role", "")))
        if key in records:
            raise AcquisitionError(f"Duplicate optical acquisition entry: {key[0]}:{key[1]}")
        records[key] = raw
    missing = sorted(expected - set(records))
    extra = sorted(set(records) - expected)
    if missing or extra:
        raise AcquisitionError(
            "Optical acquisition manifest does not exactly match selected scene assets: "
            f"missing={len(missing)}, extra={len(extra)}. Re-run acquire-optical."
        )
    cache_root = root / "data" / "interim" / region_id / "optical" / "clips"
    checksums: dict[str, str] = {}
    total_bytes = 0
    for key in sorted(expected):
        record = records[key]
        relative = Path(str(record.get("path", "")))
        file_path = root / relative
        if not _inside(file_path, cache_root):
            raise AcquisitionError(f"Optical acquisition path escapes the clip cache: {relative}")
        if not file_path.is_file():
            raise AcquisitionError(f"Optical clip is missing: {relative}. Re-run acquire-optical.")
        size = file_path.stat().st_size
        if size != int(record.get("bytes", -1)):
            raise AcquisitionError(
                f"Optical clip size changed: {relative}. Re-run acquire-optical."
            )
        checksum = sha256_file(file_path)
        if checksum != record.get("sha256"):
            raise AcquisitionError(
                f"Optical clip checksum changed: {relative}. Re-run acquire-optical."
            )
        checksums[relative.as_posix()] = checksum
        total_bytes += size
    return ValidatedOpticalCache(
        manifest_path=path,
        manifest_checksum=sha256_file(path),
        file_count=len(expected),
        total_bytes=total_bytes,
        file_checksums=checksums,
    )


def cached_outputs_are_valid(manifest: dict[str, Any], root: Path) -> bool:
    outputs = manifest.get("output_files")
    checksums = manifest.get("output_checksums")
    if not isinstance(outputs, list) or not isinstance(checksums, dict) or not outputs:
        return False
    for relative_text in outputs:
        relative = Path(str(relative_text))
        path = root / relative
        if not _inside(path, root) or not path.is_file():
            return False
        if checksums.get(relative.as_posix()) != sha256_file(path):
            return False
    return True
