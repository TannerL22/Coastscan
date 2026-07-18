"""Live STAC asset-contract inspection and stable metadata extraction."""

import json
from typing import Any

from coastscan.models.optical import OpticalAssets

ASSET_ROLES = {
    "blue": "continuous",
    "green": "continuous",
    "red": "continuous",
    "nir": "continuous",
    "swir1": "continuous",
    "scene_classification": "categorical",
}


def required_asset_map(assets: OpticalAssets) -> dict[str, str]:
    return {
        "blue": assets.blue,
        "green": assets.green,
        "red": assets.red,
        "nir": assets.nir,
        "swir1": assets.swir1,
        "scene_classification": assets.scene_classification,
    }


def stable_asset_metadata(item_assets: dict[str, Any], mapping: OpticalAssets) -> str:
    selected: dict[str, object] = {}
    for role, key in required_asset_map(mapping).items():
        asset = item_assets.get(key, {})
        selected[role] = {
            "key": key,
            "href": asset.get("href"),
            "file_checksum": asset.get("file:checksum"),
            "file_size": asset.get("file:size"),
            "gsd": asset.get("gsd"),
            "data_type": asset.get("data_type"),
            "nodata": asset.get("nodata"),
            "scale": asset.get("raster:scale"),
            "offset": asset.get("raster:offset"),
            "proj_code": asset.get("proj:code"),
            "proj_shape": asset.get("proj:shape"),
            "proj_transform": asset.get("proj:transform"),
            "resampling": "nearest" if ASSET_ROLES[role] == "categorical" else "bilinear",
        }
    return json.dumps(selected, sort_keys=True, separators=(",", ":"))
