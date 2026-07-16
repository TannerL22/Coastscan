"""Safe downloads through the public CNIG product-page workflow."""

import hashlib
import http.cookiejar
import json
import os
import zipfile
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import rasterio

from coastscan.catalog.manifests import sha256_file
from coastscan.exceptions import AcquisitionError

CNIG_BASE = "https://centrodedescargas.cnig.es/CentroDescargas"


class ResponseLike(Protocol):
    def read(self, size: int = -1) -> bytes: ...


def safe_write_response(
    response: ResponseLike,
    destination: Path,
    expected_checksum: str | None = None,
) -> str:
    """Stream to `.part`, validate checksum, then atomically publish the final file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with part.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                digest.update(chunk)
        checksum = digest.hexdigest()
        if expected_checksum and checksum.lower() != expected_checksum.lower():
            raise AcquisitionError(
                f"Checksum mismatch for {destination.name}: expected {expected_checksum}, "
                f"got {checksum}"
            )
        if destination.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(part) as archive:
                    failing_member = archive.testzip()
                if failing_member:
                    raise AcquisitionError(
                        f"Archive integrity failure in {destination.name}: {failing_member}"
                    )
            except zipfile.BadZipFile as exc:
                raise AcquisitionError(f"Downloaded archive is invalid: {destination}") from exc
        elif destination.suffix.lower() in {".tif", ".tiff"}:
            try:
                with rasterio.open(part) as dataset:
                    if dataset.crs is None or dataset.width <= 0 or dataset.height <= 0:
                        raise AcquisitionError(
                            f"Downloaded raster metadata is invalid: {destination}"
                        )
            except rasterio.errors.RasterioError as exc:
                raise AcquisitionError(f"Downloaded raster is invalid: {destination}") from exc
        os.replace(part, destination)
        return checksum
    except Exception:
        part.unlink(missing_ok=True)
        raise


def validate_existing(path: Path, expected_checksum: str | None) -> str | None:
    if not path.is_file():
        return None
    checksum = sha256_file(path)
    if expected_checksum and checksum.lower() != expected_checksum.lower():
        return None
    return checksum


def download_cnig_resource(
    sequential_id: str,
    destination: Path,
    *,
    expected_checksum: str | None = None,
    timeout_seconds: float = 120.0,
) -> tuple[str, bool]:
    """Download one public CNIG resource; return checksum and reuse flag."""
    existing = validate_existing(destination, expected_checksum)
    if existing:
        return existing, True
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    try:
        opener.open(
            f"{CNIG_BASE}/detalleArchivo?sec={sequential_id}", timeout=timeout_seconds
        ).close()
        init_response = opener.open(
            f"{CNIG_BASE}/initDescargaDir?secuencial={sequential_id}",
            timeout=timeout_seconds,
        )
        initialization = json.loads(init_response.read().decode("utf-8"))
        init_response.close()
        if initialization.get("muestraLic") != "NO":
            raise AcquisitionError(
                f"CNIG resource {sequential_id} requires manual licence interaction"
            )
        payload = urlencode({"secDescDirLA": initialization["secuencialDescDir"]}).encode("ascii")
        request = Request(f"{CNIG_BASE}/descargaDir", data=payload, method="POST")
        response = opener.open(request, timeout=timeout_seconds)
        try:
            checksum = safe_write_response(response, destination, expected_checksum)
        finally:
            response.close()
        return checksum, False
    except AcquisitionError:
        raise
    except Exception as exc:
        raise AcquisitionError(
            f"Official CNIG download failed for resource {sequential_id}: {exc}. "
            "Use the manual instructions in docs/mallorca_phase1_data.md."
        ) from exc


def extract_zip_safely(path: Path, destination: Path) -> None:
    """Extract without path traversal while preserving the downloaded archive."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(path) as archive:
        for member in sorted(archive.infolist(), key=lambda item: item.filename):
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise AcquisitionError(f"Unsafe archive member path: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
