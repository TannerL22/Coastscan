"""Validation for small, publicly committed derived-data snapshots."""

import json
import re
from pathlib import Path
from typing import Any

from coastscan.catalog.manifests import sha256_file
from coastscan.config import PROJECT_ROOT
from coastscan.exceptions import CoastScanError

SNAPSHOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
REQUIRED_FIELDS = {
    "snapshot_id",
    "region_id",
    "snapshot_purpose",
    "generated_at_utc",
    "published_at_utc",
    "generation_git_commit",
    "publication_git_commit",
    "upstream_phase1_manifest",
    "upstream_phase2_manifest",
    "included_files",
    "file_sizes",
    "sha256_checksums",
    "source_datasets",
    "source_providers",
    "source_releases",
    "licences",
    "required_attribution",
    "derived_output_statement",
    "excluded_source_data",
    "redistribution_review",
    "viewer_schema_version",
    "notes",
}


def _manifest_path(snapshot: str, root: Path) -> Path:
    identifier = snapshot.removesuffix(".json")
    if not SNAPSHOT_ID_PATTERN.fullmatch(identifier):
        raise CoastScanError(
            "Snapshot must be an identifier containing only letters, numbers, hyphens or "
            "underscores."
        )
    return root / "data_catalog" / "published_snapshots" / f"{identifier}.json"


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CoastScanError(f"Published snapshot manifest not found: {path}")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoastScanError(f"Published snapshot manifest is not valid JSON: {path}") from exc
    if not isinstance(parsed, dict):
        raise CoastScanError(f"Published snapshot manifest must be a JSON object: {path}")
    return parsed


def validate_published_snapshot(
    snapshot: str,
    root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    """Verify the manifest contract, file sizes and SHA-256 checksums."""
    project_root = root.resolve()
    manifest_path = _manifest_path(snapshot, project_root)
    manifest = _load_manifest(manifest_path)
    missing_fields = sorted(REQUIRED_FIELDS - set(manifest))
    if missing_fields:
        raise CoastScanError(
            "Published snapshot manifest is missing required fields: " + ", ".join(missing_fields)
        )

    included = manifest["included_files"]
    sizes = manifest["file_sizes"]
    checksums = manifest["sha256_checksums"]
    if not isinstance(included, list) or not all(isinstance(path, str) for path in included):
        raise CoastScanError("Published snapshot included_files must be a list of relative paths.")
    if len(set(included)) != len(included):
        raise CoastScanError("Published snapshot included_files contains duplicate paths.")
    if not isinstance(sizes, dict) or not isinstance(checksums, dict):
        raise CoastScanError("Published snapshot sizes and checksums must be path-keyed objects.")
    if set(included) != set(sizes) or set(included) != set(checksums):
        raise CoastScanError(
            "Published snapshot included_files, file_sizes and sha256_checksums keys differ."
        )

    errors: list[str] = []
    total_bytes = 0
    for relative in included:
        path = (project_root / relative).resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            errors.append(f"path escapes project root: {relative}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {relative}")
            continue
        actual_size = path.stat().st_size
        total_bytes += actual_size
        if sizes[relative] != actual_size:
            errors.append(
                f"size mismatch: {relative} (expected {sizes[relative]}, got {actual_size})"
            )
        actual_checksum = sha256_file(path)
        if checksums[relative] != actual_checksum:
            errors.append(
                f"checksum mismatch: {relative} (expected {checksums[relative]}, "
                f"got {actual_checksum})"
            )
    if errors:
        raise CoastScanError("Published snapshot validation failed: " + "; ".join(errors))

    return {
        "snapshot_id": manifest["snapshot_id"],
        "manifest": str(manifest_path.relative_to(project_root)),
        "checked_files": len(included),
        "total_bytes": total_bytes,
        "status": "pass",
    }
