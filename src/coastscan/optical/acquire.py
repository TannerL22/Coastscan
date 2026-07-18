"""Official CDSE S3, AOI-windowed Sentinel-2 asset acquisition."""

import json
import os
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.mask import mask

from coastscan.catalog.manifests import sha256_file
from coastscan.config import PROJECT_ROOT, load_region_config
from coastscan.exceptions import AcquisitionError
from coastscan.optical.authentication import require_s3_credentials
from coastscan.optical.cache import CLIP_ROLES, acquisition_manifest_path, clip_path
from coastscan.optical.catalogue import discover_scene_catalogue


def _vsi_path(href: str) -> str:
    if not href.startswith("s3://eodata/"):
        raise AcquisitionError(f"Expected a stable official s3://eodata asset, got {href[:40]!r}")
    return "/vsis3/" + href.removeprefix("s3://")


def _atomic_clip(
    href: str, destination: Path, aoi: gpd.GeoDataFrame, options: dict[str, object]
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part.tif")
    try:
        with rasterio.Env(**options), rasterio.open(_vsi_path(href)) as source:
            projected = aoi.to_crs(source.crs)
            values, transform = mask(source, projected.geometry, crop=True, filled=True)
            profile = source.profile.copy()
            profile.update(
                driver="GTiff",
                height=values.shape[1],
                width=values.shape[2],
                transform=transform,
                tiled=True,
                compress="deflate",
                BIGTIFF="IF_SAFER",
            )
            with rasterio.open(temporary, "w", **profile) as target:
                target.write(values)
        os.replace(temporary, destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise AcquisitionError(f"Official Copernicus asset read failed for {href}: {exc}") from exc


def acquire_optical(
    region: str | Path,
    *,
    root: Path = PROJECT_ROOT,
    refresh_catalogue: bool = False,
) -> dict[str, object]:
    credentials = require_s3_credentials()
    config, _ = load_region_config(region, root)
    source = config.inputs.optical
    if source is None or config.area_of_interest is None:
        raise AcquisitionError("Optical input and AOI must be configured")
    scenes, metadata = discover_scene_catalogue(region, root=root, refresh=refresh_catalogue)
    selected = scenes.loc[scenes.selected].sort_values(["acquisition_datetime_utc", "scene_id"])
    estimated_clipped_bytes = int(
        (selected.estimated_source_bytes * selected.aoi_to_scene_area_share).sum()
    )
    cache_limit = int(config.optical.maximum_optical_cache_gb * 1024**3) if config.optical else 0
    if estimated_clipped_bytes > cache_limit:
        raise AcquisitionError(
            f"Estimated AOI-clipped assets require {estimated_clipped_bytes / 1024**3:.2f} GiB, "
            f"above the configured {cache_limit / 1024**3:.2f} GiB optical cache ceiling."
        )
    aoi_path = root / config.area_of_interest.path
    aoi = gpd.read_file(aoi_path, layer=config.area_of_interest.layer)
    cache = root / "data" / "interim" / config.region_id / "optical" / "clips"
    manifest_path = acquisition_manifest_path(root, config.region_id)
    previous_files: dict[tuple[str, str], dict[str, object]] = {}
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if previous.get("catalogue_checksum") == metadata.get("catalogue_checksum"):
            previous_files = {
                (str(item.get("scene_id")), str(item.get("asset_role"))): item
                for item in previous.get("files", [])
                if isinstance(item, dict)
            }
    files: list[dict[str, object]] = []
    asset_columns = [f"asset_{role}" for role in CLIP_ROLES]
    for row in selected.itertuples():
        for column in asset_columns:
            href = str(getattr(row, column))
            role = column.removeprefix("asset_")
            destination = clip_path(root, config.region_id, str(row.scene_id), role)
            previous = previous_files.get((str(row.scene_id), role), {})
            cached_valid = (
                destination.is_file()
                and previous.get("source_href") == href
                and previous.get("bytes") == destination.stat().st_size
                and previous.get("sha256") == sha256_file(destination)
            )
            if not cached_valid:
                _atomic_clip(
                    href, destination, aoi, credentials.rasterio_options(source.s3_endpoint)
                )
            metadata_role = "scene_classification" if role == "scl" else role
            source_metadata = json.loads(str(row.asset_checksums_or_etags)).get(metadata_role, {})
            files.append(
                {
                    "scene_id": str(row.scene_id),
                    "asset_role": role,
                    "path": destination.relative_to(root).as_posix(),
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                    "source_href": href,
                    "source_asset_checksum": source_metadata.get("file_checksum"),
                    "source_asset_size": source_metadata.get("file_size"),
                }
            )
            actual_bytes = sum(path.stat().st_size for path in cache.glob("*/*.tif"))
            if actual_bytes > cache_limit:
                raise AcquisitionError(
                    "Optical cache exceeded its configured ceiling; completed clips remain "
                    "recoverable and no source imagery was committed."
                )
    manifest = {
        "region_id": config.region_id,
        "provider": "Copernicus Data Space Ecosystem",
        "catalogue_checksum": metadata.get("catalogue_checksum"),
        "query_fingerprint": metadata.get("query_fingerprint"),
        "scene_count": len(selected),
        "files": files,
        "actual_cache_bytes": sum(
            destination.stat().st_size for destination in cache.glob("*/*.tif")
        ),
        "estimated_clipped_bytes": estimated_clipped_bytes,
        "configured_cache_limit_bytes": cache_limit,
        "secrets_recorded": False,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest_path.with_suffix(".part.json")
    temporary_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)
    return manifest
