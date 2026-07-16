"""Atomic downloads from stable official HTTPS endpoints."""

from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from coastscan.acquire.cnig import safe_write_response, validate_existing
from coastscan.exceptions import AcquisitionError


def download_https_resource(
    url: str,
    destination: Path,
    *,
    expected_checksum: str | None = None,
    timeout_seconds: float = 300.0,
) -> tuple[str, bool, dict[str, str]]:
    """Download an official HTTPS resource atomically, returning response metadata."""
    if not url.lower().startswith("https://"):
        raise AcquisitionError(f"Only HTTPS acquisition is allowed: {url}")
    existing = validate_existing(destination, expected_checksum)
    if existing:
        return existing, True, {"validation": "local checksum", "url": url}
    request = Request(url, headers={"User-Agent": "CoastScan/0.2 official-data acquisition"})
    try:
        response = urlopen(request, timeout=timeout_seconds)  # noqa: S310
        try:
            metadata = {
                "url": response.geturl(),
                "content_type": response.headers.get("Content-Type", ""),
                "content_length": response.headers.get("Content-Length", ""),
                "etag": response.headers.get("ETag", ""),
                "last_modified": response.headers.get("Last-Modified", ""),
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
            }
            checksum = safe_write_response(response, destination, expected_checksum)
        finally:
            response.close()
        return checksum, False, metadata
    except AcquisitionError:
        raise
    except Exception as exc:
        raise AcquisitionError(f"Official HTTPS download failed for {url}: {exc}") from exc
