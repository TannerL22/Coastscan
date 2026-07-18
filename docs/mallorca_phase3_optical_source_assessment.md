# Mallorca Phase 3 official optical-source assessment

Assessment date: 2026-07-18

Primary official references: [CDSE STAC](https://documentation.dataspace.copernicus.eu/APIs/STAC.html),
[CDSE S3](https://documentation.dataspace.copernicus.eu/APIs/S3.html),
[CDSE quotas](https://documentation.dataspace.copernicus.eu/Quotas.html),
[CDSE terms](https://dataspace.copernicus.eu/terms-and-conditions), and the
[Sentinel-2 products specification](https://sentiwiki.copernicus.eu/web/s2-products).

## Selected official service

| Field | Verified selection |
|---|---|
| Provider | Copernicus Data Space Ecosystem (CDSE) |
| Catalogue endpoint | `https://stac.dataspace.copernicus.eu/v1` |
| Collection | `sentinel-2-l2a` |
| Product level | Sentinel-2 MSI Level-2A, bottom-of-atmosphere surface reflectance |
| Asset format | JPEG 2000 assets in the official SAFE product, exposed by stable `s3://eodata/...` STAC assets and authenticated HTTPS alternates |
| Authentication | Public STAC catalogue; generated CDSE S3 access/secret credentials for pixel access |
| Storage endpoint | `https://eodata.dataspace.copernicus.eu` |
| Acquisition method | AOI-windowed reads of required official S3 JP2 assets through Rasterio/GDAL; clipped local GeoTIFF cache |
| Historical baseline | 2021-01-01 through 2025-12-31; May through September for the principal travel-period screen |

The live official STAC collection and Mallorca items were inspected before configuration. The pilot is
covered by MGRS tile `31TDE`. The selected live asset keys are:

The reproducible 2026-07-18 catalogue refresh returned 394 items for 2021–2025. The configured
May–September and 80% catalogue-cloud ceiling selected 142 scenes and rejected 252 (225 outside the
configured months and 27 above the initial cloud ceiling). Selected coverage runs from 2021-05-07 to
2025-09-30. Processing baselines are 05.00 (22), 05.10 (67) and 05.11 (53); every returned item has
all six required analytical assets. Full selected assets total an estimated 63,524,349,218 bytes,
while AOI-area-proportional clipped storage is approximately 640,738,856 bytes. The catalogue SHA-256
is `3a6d0bced8ab853be6f60249a62ce349ec888678c57e5303240604123618967e`.

| Role | STAC asset | Native sampling | Handling |
|---|---|---:|---|
| Blue | `B02_10m` | 10 m | continuous, bilinear only when alignment requires resampling |
| Green | `B03_10m` | 10 m | continuous, bilinear |
| Red | `B04_10m` | 10 m | continuous, bilinear |
| NIR | `B08_10m` | 10 m | continuous, bilinear |
| SWIR1 | `B11_20m` | 20 m | continuous, bilinear; remains documented as native 20 m information |
| Scene classification | `SCL_20m` | 20 m | categorical, nearest-neighbour only |
| Product metadata | `product_metadata` | n/a | stable metadata reference |
| Granule metadata | `granule_metadata` | n/a | stable metadata and geometry reference |

Live item metadata supplies `raster:scale`, `raster:offset`, nodata, data type, file size, SHA-style
checksum, projected CRS/shape/transform, processing baseline, solar/view geometry and classification
class definitions. A current PB 05.11 item reports scale `0.0001` and offset `-0.1`; the pipeline reads
these values per asset rather than assuming an invariant digital-number conversion. PB 04.00 introduced
the radiometric offset evolution, so processing-baseline metadata is retained and tested.

## Scene classification and masks

The current `SCL_20m` asset defines classes 0 through 11. CoastScan rejects no-data (0), saturated or
defective (1), cloud shadow (3), medium-probability cloud (8), high-probability cloud (9), thin cirrus
(10), and snow/ice (11). It retains water (6) and does not automatically discard unclassified class 7;
retained pixels must still pass vector land exclusion, spectral water validation and the conservative
dark-shadow, whitewater and glint-risk heuristics. PSD 15 renamed class 2 from dark features to cast
shadow for PB 05.11 without changing the computation; CoastScan treats it as a shadow-risk input, not
as automatically valid dark water.

## Licence and attribution

Copernicus Sentinel data access and use is free, full and open under the official Sentinel Data Legal
Notice referenced by the CDSE terms. Derived CoastScan tables must acknowledge Copernicus Sentinel-2
and the Copernicus Data Space Ecosystem. Raw imagery, JP2 assets, source SAFE products and local QA
cutouts are not part of the published viewer snapshot.

Required project attribution:

> Contains modified Copernicus Sentinel-2 data (2021-2025), accessed through the Copernicus Data
> Space Ecosystem.

## Quotas and operational limits

The CDSE general-user table currently documents an S3 request limit of 2,000 per minute, four
concurrent immediately-available-data connections, 20 MB/s per connection and a 12 TB rolling
30-day transfer threshold. CoastScan is deliberately single-pilot and sequential by default, caches
completed clips, uses deterministic keys and enforces a configured local cache ceiling. It does not
attempt to bypass quota limits.

## Known limitations

- STAC imagery assets are JP2 SAFE assets, not COGs. Windowed S3 access is therefore used where GDAL
  supports it; otherwise the acquisition command stops rather than downloading complete SAFE products.
- Catalogue cloud percentage is tile-wide and is used only as an initial ceiling. Coastal AOI masks
  determine final observation validity.
- L2A is atmospheric-corrected surface reflectance, not a calibrated turbidity, Secchi-depth or
  physical underwater-visibility product.
- B11 and SCL are native 20 m assets. Alignment to the 10 m grid does not create independent 10 m
  information.
- CDSE S3 credentials require one-time account and key creation and an explicit expiry date. Secrets
  remain in environment variables and are never written to logs, catalogues or manifests.
