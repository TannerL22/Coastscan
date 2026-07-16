"""Source-catalogue records."""

from pydantic import BaseModel


class SourceRecord(BaseModel):
    source_id: str
    provider: str = ""
    dataset_name: str = ""
    version: str = ""
    source_type: str = ""
    coverage: str = ""
    native_crs: str = ""
    horizontal_resolution: str = ""
    vertical_units: str = ""
    licence: str = ""
    source_url: str = ""
    download_date: str = ""
    local_path: str = ""
    checksum: str = ""
    notes: str = ""
