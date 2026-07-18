import json
from pathlib import Path

import pytest

from coastscan.catalog.manifests import sha256_file
from coastscan.catalog.published_snapshots import validate_published_snapshot
from coastscan.exceptions import CoastScanError


def _manifest(root: Path, checksum: str) -> Path:
    included = "data/processed/demo/value.parquet"
    output = root / "data_catalog/published_snapshots/demo.json"
    output.parent.mkdir(parents=True)
    output.write_text(
        json.dumps(
            {
                "snapshot_id": "demo",
                "region_id": "demo",
                "snapshot_purpose": "test",
                "generated_at_utc": "2026-01-01T00:00:00Z",
                "published_at_utc": "2026-01-01T00:00:00Z",
                "generation_git_commit": "0" * 40,
                "publication_git_commit": "1" * 40,
                "upstream_phase1_manifest": "phase1.json",
                "upstream_phase2_manifest": "phase2.json",
                "included_files": [included],
                "file_sizes": {included: 7},
                "sha256_checksums": {included: checksum},
                "source_datasets": [],
                "source_providers": [],
                "source_releases": {},
                "licences": {},
                "required_attribution": [],
                "derived_output_statement": "derived",
                "excluded_source_data": [],
                "redistribution_review": {"status": "approved"},
                "viewer_schema_version": "test",
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    return output


def test_validate_published_snapshot_checks_size_and_checksum(tmp_path: Path) -> None:
    published = tmp_path / "data/processed/demo/value.parquet"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"derived")
    _manifest(tmp_path, sha256_file(published))

    result = validate_published_snapshot("demo", tmp_path)

    assert result == {
        "snapshot_id": "demo",
        "manifest": "data_catalog\\published_snapshots\\demo.json",
        "checked_files": 1,
        "total_bytes": 7,
        "status": "pass",
    }


def test_validate_published_snapshot_rejects_changed_file(tmp_path: Path) -> None:
    published = tmp_path / "data/processed/demo/value.parquet"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"derived")
    _manifest(tmp_path, "0" * 64)

    with pytest.raises(CoastScanError, match="checksum mismatch"):
        validate_published_snapshot("demo", tmp_path)
