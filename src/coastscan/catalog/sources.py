"""Source registry inspection."""

from pathlib import Path

import pandas as pd

from coastscan.models.sources import SourceRecord


def load_source_catalog(path: Path) -> dict[str, SourceRecord]:
    records = pd.read_csv(path, dtype=str, keep_default_na=False).to_dict(orient="records")
    return {str(record["source_id"]): SourceRecord.model_validate(record) for record in records}


def source_metadata_warnings(record: SourceRecord | None) -> list[str]:
    if record is None:
        return ["source is absent from data_catalog/sources.csv"]
    critical = ("provider", "version", "licence", "native_crs", "horizontal_resolution")
    return [
        f"source metadata {field} is missing or unconfirmed"
        for field in critical
        if not getattr(record, field) or "UNKNOWN" in getattr(record, field)
    ]
