"""Secret-safe Copernicus Data Space S3 authentication."""

import os
from dataclasses import dataclass

from rasterio.session import AWSSession

from coastscan.exceptions import AcquisitionError

ACCESS_KEY_ENV = "COPERNICUS_S3_ACCESS_KEY"
SECRET_KEY_ENV = "COPERNICUS_S3_SECRET_KEY"


@dataclass(frozen=True, repr=False)
class CopernicusS3Credentials:
    access_key: str
    secret_key: str

    def __repr__(self) -> str:
        return "CopernicusS3Credentials(access_key=<redacted>, secret_key=<redacted>)"

    def rasterio_session(self, endpoint: str) -> AWSSession:
        """Create the boto3-backed Rasterio session required by Rasterio 1.5+."""
        endpoint_url = endpoint.rstrip("/")
        return AWSSession(
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name="default",
            endpoint_url=endpoint_url,
        )

    def rasterio_options(self) -> dict[str, object]:
        """Non-secret GDAL S3 options used alongside the boto3 session."""
        return {
            "AWS_HTTPS": "YES",
            "AWS_VIRTUAL_HOSTING": "FALSE",
        }


def authentication_status() -> dict[str, object]:
    access = bool(os.environ.get(ACCESS_KEY_ENV, "").strip())
    secret = bool(os.environ.get(SECRET_KEY_ENV, "").strip())
    return {
        "method": "copernicus_s3_generated_credentials",
        "access_key_present": access,
        "secret_key_present": secret,
        "ready": access and secret,
        "secrets_redacted": True,
    }


def require_s3_credentials() -> CopernicusS3Credentials:
    access_key = os.environ.get(ACCESS_KEY_ENV, "").strip()
    secret_key = os.environ.get(SECRET_KEY_ENV, "").strip()
    if not access_key or not secret_key:
        raise AcquisitionError(
            "Official Copernicus imagery access requires generated CDSE S3 credentials. "
            "Create an account at https://dataspace.copernicus.eu, generate an access/secret "
            "pair at https://eodata-s3keysmanager.dataspace.copernicus.eu, then set "
            f"{ACCESS_KEY_ENV} and {SECRET_KEY_ENV}. Catalogue inspection remains public."
        )
    return CopernicusS3Credentials(access_key, secret_key)
