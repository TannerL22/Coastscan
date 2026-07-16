"""Deterministic GeoParquet output helpers."""

from pathlib import Path

import geopandas as gpd
import pandas as pd


def write_geoparquet(frame: gpd.GeoDataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = frame.sort_values(next(iter(frame.columns))).reset_index(drop=True)
    ordered.to_parquet(path, index=False, compression="zstd")
    return path


def write_parquet(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False, compression="zstd")
    return path
