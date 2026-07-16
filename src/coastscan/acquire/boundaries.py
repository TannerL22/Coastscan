"""Reproducible pilot AOI creation from documented named-endpoint bounds."""

import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box


def create_documented_aoi(definition_path: Path, destination: Path, layer: str) -> Path:
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    geometry = box(*map(float, definition["bounds_epsg4326"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(
        {
            "aoi_id": [definition["aoi_id"]],
            "definition": [definition["method"]],
        },
        geometry=[geometry],
        crs="EPSG:4326",
    ).to_file(destination, layer=layer, driver="GPKG")
    return destination
