import hashlib
import io
from pathlib import Path

import pytest
from pydantic import ValidationError

from coastscan.acquire.cnig import safe_write_response, validate_existing
from coastscan.exceptions import AcquisitionError
from coastscan.models.acquisition import RegionAcquisitionManifest


def test_complete_acquisition_manifest_validates() -> None:
    manifest = RegionAcquisitionManifest.model_validate(
        {
            "region_id": "pilot",
            "sources": [
                {
                    "source_id": "official",
                    "provider": "Authority",
                    "product_name": "Product",
                    "product_version": "2026",
                    "edition_date": "2026-01-01",
                    "official_product_reference": "https://authority.example/product",
                    "licence": "CC-BY-4.0",
                    "required_attribution": "Authority attribution",
                    "resources": [
                        {
                            "resource_name": "source.tif",
                            "cnig_sequential_id": "1",
                            "local_relative_path": "data/raw/source.tif",
                        }
                    ],
                }
            ],
        }
    )
    assert manifest.sources[0].provider == "Authority"


def test_missing_required_provenance_fails() -> None:
    with pytest.raises(ValidationError):
        RegionAcquisitionManifest.model_validate(
            {"region_id": "pilot", "sources": [{"source_id": "incomplete"}]}
        )


def test_safe_download_checksum_and_existing_reuse(tmp_path: Path) -> None:
    payload = b"authoritative bytes"
    checksum = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "source.bin"
    assert safe_write_response(io.BytesIO(payload), destination, checksum) == checksum
    assert validate_existing(destination, checksum) == checksum


def test_failed_checksum_leaves_no_complete_or_partial_file(tmp_path: Path) -> None:
    destination = tmp_path / "source.bin"
    with pytest.raises(AcquisitionError, match="Checksum mismatch"):
        safe_write_response(io.BytesIO(b"wrong"), destination, "0" * 64)
    assert not destination.exists()
    assert not destination.with_suffix(".bin.part").exists()


def test_invalid_archive_is_not_published(tmp_path: Path) -> None:
    destination = tmp_path / "source.zip"
    with pytest.raises(AcquisitionError, match="archive is invalid"):
        safe_write_response(io.BytesIO(b"not a zip"), destination)
    assert not destination.exists()
