# Mallorca Phase 3 optical pipeline

Phase 3 is an additive historical screening stage. It asks which coastline segments have most
consistently shown relatively clear-looking coastal water in valid official Sentinel-2 observations.
It does not measure current conditions, turbidity concentration, physical visibility depth,
underwater clearance, submerged obstacles, suitability or safety.

## Reproducible flow

1. `inspect-optical` validates the typed optional configuration and queries the public CDSE STAC
   catalogue. It records stable item IDs, stable `s3://eodata` assets, processing baselines,
   selection/rejection reasons, checksums, AOI coverage and estimated bytes.
2. `acquire-optical` requires runtime-only CDSE S3 access/secret credentials. It clips only the six
   required official JP2 assets to the AOI, writes atomic local GeoTIFF cache entries and records
   hashes without recording credentials. A configurable cache ceiling is enforced.
3. `build-clarity` verifies the five protected Phase 1/2 files before and after its run. It creates
   deterministic segment-owned nearshore (20–100 m), coastal (100–300 m) and context (300–750 m)
   zones from authoritative seaward orientation. Ambiguous orientations have no optical zone.
4. Native 10 m continuous bands establish the working grid. Native 20 m B11 and SCL assets are
   aligned with bilinear and nearest-neighbour resampling respectively; this does not claim new
   10 m source information. Per-asset STAC scale, offset and nodata metadata are applied.
5. SCL, spectral-water, vector-land, dark-shadow, whitewater and sunglint-risk tests create explicit
   exclusion shares and machine-readable invalid reasons. Catalogue cloud cover is never treated as
   the final coastal mask.
6. Blue/green ratio, NDTI and NIR components are directionally ranked within scene and zone type.
   Constant populations receive a neutral percentile; small comparison populations are insufficient.
7. Monthly and configured seasonal periods aggregate valid scene counts, year/month coverage,
   median and p90 relative clarity, clear/turbid-looking shares, persistence and variability.
   Confidence is calculated separately from clarity.
8. Apparent bottom texture is separately gated by valid clear-looking observations, foam/glint
   exclusions, regional texture strength and cross-scene persistence. Unstable or sparse evidence is
   reported as `unstable` or `insufficient`, not as physical bottom visibility.

The cache key includes the configuration, official catalogue, algorithm version and all five protected
Phase 1/2 checksums. A changed scene selection, asset metadata, configuration, zone contract or
upstream file therefore invalidates reuse.

## Commands and one-time account action

Catalogue inspection is public:

```powershell
uv run coastscan inspect-optical --region mallorca_northwest_pilot
```

Pixel access requires a CDSE account and generated S3 credentials from
<https://eodata-s3keysmanager.dataspace.copernicus.eu>. Set these locally and never commit them:

```powershell
$env:COPERNICUS_S3_ACCESS_KEY="..."
$env:COPERNICUS_S3_SECRET_KEY="..."
uv run coastscan acquire-optical --region mallorca_northwest_pilot
uv run coastscan build-clarity --region mallorca_northwest_pilot --force --write-observations
uv run coastscan build-clarity --region mallorca_northwest_pilot --write-observations
```

Without both credentials the acquisition command exits with an exact instruction and does not use an
unofficial mirror. Raw imagery, clipped rasters, observations, tokens and local QA cutouts are ignored
and are not part of the hosted snapshot.

## Viewer boundary

The viewer prefers the Phase 3 join only when it exists and retains Phase 2 and terrain-only fallbacks.
The period selector replaces optical attributes in memory from the seasonal table; coastline geometry
always comes from `coast_segments.parquet`. The hosted snapshot must not be expanded until the real
Mallorca run, critical visual QA, licence review and local viewer inspection all pass.
