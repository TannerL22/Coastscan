"""Public official Copernicus STAC discovery and deterministic selection."""

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from coastscan.catalog.manifests import sha256_file, sha256_text
from coastscan.config import PROJECT_ROOT, data_path, load_region_config
from coastscan.exceptions import AcquisitionError, ConfigurationError
from coastscan.optical.assets import required_asset_map, stable_asset_metadata
from coastscan.optical.authentication import authentication_status

JsonGetter = Callable[[str], dict[str, Any]]


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "CoastScan/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            parsed = json.load(response)
    except OSError as exc:
        raise AcquisitionError(f"Copernicus STAC request failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AcquisitionError("Copernicus STAC response was not a JSON object")
    return parsed


def _aoi(config: Any, root: Path) -> gpd.GeoDataFrame:
    if config.area_of_interest is None:
        raise ConfigurationError("optical discovery requires area_of_interest")
    path = data_path(config.area_of_interest.path, root)
    try:
        frame = gpd.read_file(path, layer=config.area_of_interest.layer)
    except Exception as exc:
        raise ConfigurationError(f"Could not read optical AOI {path}: {exc}") from exc
    if frame.empty or frame.crs is None or frame.geometry.is_empty.any():
        raise ConfigurationError(f"Optical AOI is empty, invalid, or has no CRS: {path}")
    return frame.to_crs("EPSG:4326")


def catalogue_query(config: Any, root: Path) -> tuple[str, str, gpd.GeoDataFrame]:
    source = config.inputs.optical
    settings = config.optical
    if source is None or settings is None:
        raise ConfigurationError(f"Optical inputs are not configured for {config.region_id}")
    aoi = _aoi(config, root)
    bbox = ",".join(f"{value:.8f}" for value in aoi.total_bounds)
    parameters = {
        "collections": source.collection,
        "bbox": bbox,
        "datetime": (
            f"{settings.historical_start.isoformat()}T00:00:00Z/"
            f"{settings.historical_end.isoformat()}T23:59:59Z"
        ),
        "limit": "100",
    }
    url = f"{source.catalogue_endpoint.rstrip('/')}/search?{urllib.parse.urlencode(parameters)}"
    fingerprint = sha256_text(
        json.dumps(
            {
                "url": url,
                "months": settings.months,
                "cloud": settings.catalogue.maximum_catalogue_cloud_cover_percent,
                "assets": required_asset_map(source.required_assets),
            },
            sort_keys=True,
        )
    )
    return url, fingerprint, aoi


def _processing_tuple(value: object) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return (0,)


def _rows(
    items: list[dict[str, Any]],
    config: Any,
    aoi: gpd.GeoDataFrame,
    retrieved_at: str,
) -> pd.DataFrame:
    source = config.inputs.optical
    settings = config.optical
    assert source is not None and settings is not None
    aoi_projected = aoi.to_crs(config.analysis_crs)
    aoi_geometry = aoi_projected.geometry.union_all()
    mapping = required_asset_map(source.required_assets)
    records: list[dict[str, object]] = []
    for item in items:
        properties = item.get("properties", {})
        timestamp = pd.Timestamp(properties.get("datetime"))
        assets = item.get("assets", {})
        geometry = (
            gpd.GeoSeries([shape(item["geometry"])], crs="EPSG:4326")
            .to_crs(config.analysis_crs)
            .iloc[0]
        )
        intersection_area = float(geometry.intersection(aoi_geometry).area)
        coverage = float(intersection_area / aoi_geometry.area)
        scene_area_share = float(intersection_area / geometry.area) if geometry.area else 0.0
        available = all(key in assets and assets[key].get("href") for key in mapping.values())
        cloud = float(properties.get("eo:cloud_cover", 100.0))
        in_month = int(timestamp.month) in settings.months
        under_cloud = cloud <= settings.catalogue.maximum_catalogue_cloud_cover_percent
        reason = (
            "candidate"
            if available and in_month and under_cloud and coverage > 0
            else "missing_required_asset"
            if not available
            else "outside_configured_months"
            if not in_month
            else "catalogue_cloud_cover_above_limit"
            if not under_cloud
            else "no_aoi_overlap"
        )
        record: dict[str, object] = {
            "scene_id": str(item["id"]),
            "provider_item_id": str(item["id"]),
            "collection": str(item.get("collection", source.collection)),
            "acquisition_datetime_utc": timestamp.isoformat(),
            "acquisition_date": timestamp.date().isoformat(),
            "year": int(timestamp.year),
            "month": int(timestamp.month),
            "tile_id": str(properties.get("grid:code", "")).removeprefix("MGRS-"),
            "processing_baseline": str(properties.get("processing:version", "unknown")),
            "product_uri": str(assets.get("Product", {}).get("href", "")),
            "catalogue_cloud_cover_percent": cloud,
            "aoi_coverage_share": coverage,
            "aoi_to_scene_area_share": scene_area_share,
            "sun_zenith": 90.0 - float(properties.get("view:sun_elevation", 90.0)),
            "sun_azimuth": properties.get("view:sun_azimuth"),
            "view_zenith": properties.get("view:incidence_angle"),
            "view_azimuth": properties.get("view:azimuth"),
            "asset_checksums_or_etags": stable_asset_metadata(assets, source.required_assets),
            "catalogue_url_reference": (
                f"{source.catalogue_endpoint.rstrip('/')}/collections/{source.collection}/items/"
                f"{item['id']}"
            ),
            "required_assets_available": available,
            "estimated_source_bytes": int(
                sum(int(assets.get(key, {}).get("file:size", 0) or 0) for key in mapping.values())
            ),
            "selected": reason == "candidate",
            "selection_reason": reason,
            "duplicate_group": f"{timestamp.isoformat()}|{properties.get('grid:code', '')}",
            "retrieved_at_utc": retrieved_at,
        }
        for role, key in mapping.items():
            record[f"asset_{'scl' if role == 'scene_classification' else role}"] = str(
                assets.get(key, {}).get("href", "")
            )
        records.append(record)
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    candidate = frame[frame.selection_reason == "candidate"].copy()
    if settings.catalogue.deduplicate_same_datetime:
        for _, group in candidate.groupby("duplicate_group", sort=True):
            ordered = sorted(
                group.index,
                key=lambda index: (
                    _processing_tuple(frame.at[index, "processing_baseline"]),
                    bool(frame.at[index, "required_assets_available"]),
                    float(cast(Any, frame.at[index, "aoi_coverage_share"])),
                    -float(cast(Any, frame.at[index, "catalogue_cloud_cover_percent"])),
                    str(frame.at[index, "provider_item_id"]),
                ),
                reverse=True,
            )
            for index in ordered[1:]:
                frame.at[index, "selected"] = False
                frame.at[index, "selection_reason"] = "duplicate_lower_preference"
    selected_indices = frame.index[frame.selected].tolist()
    if len(selected_indices) > settings.catalogue.maximum_items:
        ordered = sorted(
            selected_indices,
            key=lambda index: (
                str(frame.at[index, "acquisition_datetime_utc"]),
                str(frame.at[index, "provider_item_id"]),
            ),
        )
        for index in ordered[settings.catalogue.maximum_items :]:
            frame.at[index, "selected"] = False
            frame.at[index, "selection_reason"] = "maximum_items_limit"
    return frame.sort_values(
        ["acquisition_datetime_utc", "tile_id", "provider_item_id"]
    ).reset_index(drop=True)


def discover_scene_catalogue(
    region: str | Path,
    *,
    root: Path = PROJECT_ROOT,
    refresh: bool = False,
    getter: JsonGetter = _get_json,
) -> tuple[pd.DataFrame, dict[str, object]]:
    config, config_path = load_region_config(region, root)
    source = config.inputs.optical
    settings = config.optical
    if source is None or settings is None:
        raise ConfigurationError(f"Optical inputs are not configured for {config.region_id}")
    url, query_fingerprint, aoi = catalogue_query(config, root)
    directory = root / "data_catalog" / "optical"
    catalogue_path = directory / f"{config.region_id}_scenes.parquet"
    metadata_path = directory / f"{config.region_id}_scenes.json"
    if not refresh and catalogue_path.is_file() and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("query_fingerprint") == query_fingerprint:
            frame = pd.read_parquet(catalogue_path)
            return frame, {**metadata, "catalogue_reused": True}

    started = time.perf_counter()
    items: list[dict[str, Any]] = []
    next_url: str | None = url
    seen_urls: set[str] = set()
    while next_url and next_url not in seen_urls:
        seen_urls.add(next_url)
        response = getter(next_url)
        features = response.get("features", [])
        if not isinstance(features, list):
            raise AcquisitionError("Copernicus STAC features field is not a list")
        items.extend(item for item in features if isinstance(item, dict))
        next_links = [
            link.get("href")
            for link in response.get("links", [])
            if isinstance(link, dict) and link.get("rel") == "next"
        ]
        next_url = str(next_links[0]) if next_links else None
        if len(items) > 5000:
            raise AcquisitionError("Copernicus STAC returned more than the 5,000-item safety limit")
    retrieved_at = datetime.now(UTC).isoformat()
    frame = _rows(items, config, aoi, retrieved_at)
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(catalogue_path, index=False, compression="zstd")
    metadata = {
        "region_id": config.region_id,
        "provider": "Copernicus Data Space Ecosystem",
        "catalogue_endpoint": source.catalogue_endpoint,
        "collection": source.collection,
        "query_fingerprint": query_fingerprint,
        "configuration_path": config_path.relative_to(root).as_posix(),
        "configuration_checksum": sha256_file(config_path),
        "retrieved_at_utc": retrieved_at,
        "catalogue_search_seconds": time.perf_counter() - started,
        "candidate_items_returned": len(items),
        "selected_items": int(frame.selected.sum()) if not frame.empty else 0,
        "catalogue_reused": False,
    }
    metadata["catalogue_checksum"] = sha256_file(catalogue_path)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return frame, metadata


def inspect_optical(
    region: str | Path,
    *,
    root: Path = PROJECT_ROOT,
    refresh: bool = False,
    getter: JsonGetter = _get_json,
) -> dict[str, object]:
    frame, metadata = discover_scene_catalogue(region, root=root, refresh=refresh, getter=getter)
    selected = frame[frame.selected] if not frame.empty else frame
    required_columns = [column for column in frame.columns if column.startswith("asset_")]
    asset_availability = {
        column: int(frame[column].astype(str).ne("").sum()) for column in required_columns
    }
    return {
        **metadata,
        "authentication": authentication_status(),
        "tiles": sorted(selected.tile_id.dropna().astype(str).unique()) if len(selected) else [],
        "date_coverage": {
            "minimum": selected.acquisition_date.min() if len(selected) else None,
            "maximum": selected.acquisition_date.max() if len(selected) else None,
        },
        "candidate_items": len(frame),
        "selected_items": len(selected),
        "rejected_items": int((~frame.selected).sum()) if len(frame) else 0,
        "cloud_cover_percent": {
            "p10": float(selected.catalogue_cloud_cover_percent.quantile(0.1))
            if len(selected)
            else None,
            "p50": float(selected.catalogue_cloud_cover_percent.quantile(0.5))
            if len(selected)
            else None,
            "p90": float(selected.catalogue_cloud_cover_percent.quantile(0.9))
            if len(selected)
            else None,
        },
        "processing_baselines": selected.processing_baseline.value_counts().to_dict()
        if len(selected)
        else {},
        "required_asset_availability": asset_availability,
        "estimated_selected_source_bytes": int(selected.estimated_source_bytes.sum())
        if len(selected)
        else 0,
        "estimated_aoi_clipped_bytes": int(
            (selected.estimated_source_bytes * selected.aoi_to_scene_area_share).sum()
        )
        if len(selected)
        else 0,
        "duplicate_or_overlap_rejections": int(
            frame.selection_reason.astype(str).str.startswith("duplicate").sum()
        )
        if len(frame)
        else 0,
        "scene_catalogue_exists": True,
    }
