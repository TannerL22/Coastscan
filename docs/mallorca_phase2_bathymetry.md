# Mallorca northwest Phase 2 bathymetry

## Scope and selected product

Phase 2 adds regional offshore-morphology proxies to the verified Phase 1 Mallorca northwest pilot. It
does not alter Phase 1 files and does not identify safe sites.

Selected source: **EMODnet Digital Bathymetry DTM 2024, LAT**, DOI
`10.12770/cf51df64-56f9-4a99-b1aa-36b8d7b743a1`. The official 1/16 arc-minute grid is approximately
115 m at Mallorca. Mean Sea Level is also published, but this run consistently uses Lowest
Astronomical Tide and never mixes datums.

The source assessment is in `mallorca_phase2_bathymetry_source_assessment.md`. The official EMODnet
HR-DTM catalogue has no public AOI overlay. SeaDataNet `DTM-MALLORCA-2018` is request-only, MSL and
describes 100–850 m continental-slope depths rather than this nearshore corridor.

## Acquisition, licence and raw schema

`coastscan acquire-region-data` dispatches each resource by method. The selected subset comes directly
from the official EMODnet ERDDAP `bathymetry_dtm_2024` grid, is written through a partial file, checked
and atomically published. The verified subset is 709,876 bytes with SHA-256
`053afb09d1c9a6d49161d7f4a62389c61c74a43f302307fdb5e783441500981f`.

EMODnet products are CC BY 4.0. Required attribution:

> This data product was created by EMODnet (https://emodnet.ec.europa.eu/en/), and is owned by the EU
> and licensed under the Creative Commons Attribution 4.0 International (CC BY 4.0) license.

| Source variable | Canonical field | Treatment |
|---|---|---|
| `elevation` | `depth_mean_m` | explicit negative-elevation to positive-down conversion |
| `elevation_max` | `depth_min_m` | sign inverted; ordering changes under inversion |
| `elevation_min` | `depth_max_m` | sign inverted; ordering changes under inversion |
| `stdev` | `depth_std_m` | non-negative continuous value; sparse |
| `value_count` | `observation_count` | retained; never inferred from resolution |
| `interpolation_flag` | `interpolation_flag` | categorical nearest-neighbour; 1 means interpolated or extrapolated |
| `cdi_index` | `source_reference` | official index retained without invented decoding |

The NetCDF advertises EPSG:4326 in CF/EMODnet metadata but GDAL does not attach a CRS object to its
subdatasets. The adapter uses those explicit CRS and coordinate tags rather than guessing from values.
Positive topographic cells and nodata remain missing. Zero remains a valid shoreline-level value.

## Processing and feature definitions

The cache key includes the source checksum, adapter version, variable mapping, vertical conversion,
AOI/segment geometry and IDs, analysis CRS, native/output resolution and bathymetry configuration. The
grid is clipped to the offshore corridor and reprojected into EPSG:25831 at 115 m; it is not upsampled
to claim finer information. Continuous variables use bilinear resampling and require valid neighbours.
Categorical variables use nearest-neighbour. All canonical layers share one grid.

Phase 2 transects use 50 m along-coast spacing and extend 1,200 m from local Phase 1 seaward normals.
The three ambiguous Phase 1 segments create no bathymetry transects. Continuous samples use an
effective spacing no finer than 115 m, while exact configured target distances are sampled as explicit
proxies. Smaller first-valid search steps locate the coastal gap but are not independent observations.

Targets are 100, 250, 500 and 1,000 m. Every feature retains valid share, resolution ratio and class.
The 100 m ratio is 0.87 and `below_native_resolution`; longer targets are `well_resolved` only for
regional comparisons. Gradients require valid same-datum endpoints. `gradient_100_500m` is therefore
deliberately unavailable for this source, while `gradient_250_1000m` is supported. Approximate 5, 10,
20 and 30 m contour-distance proxies carry 115 m cell-scale uncertainty.

`broad_shallow_platform_proxy` means at least 60% of valid transects remain shallower than 10 m at
500 m, provided segment coverage meets 60%. It is not proof of water depth at a cliff edge.

## Screening classes

- `local_morphology_candidate`: genuinely high-resolution, complete, known-provenance coastal data.
- `coastal_context`: moderate-resolution authoritative shelf-form context.
- `regional_screening`: harmonised regional comparison data.
- `background_only`: global fallback, predominantly interpolated/extrapolated or too coarse for local form.
- `insufficient`: coverage, orientation or validity below the configured minimum.

Official WFS source-reference spot checks identify GEBCO 2024 fallback across most of the pilot, with
sparse quality fields. Configuration caps this source at `background_only`; no quality score or unknown
source type is fabricated.

## Verified real-run findings

The corrected forced and cached review runs produced 739 stable Phase 2 transects for 171 resolved or
fallback-resolved segments; three ambiguous segments were excluded. Of those, 590 found valid
bathymetry within 400 m and 149 did not. First-valid distance was 150 m at p50, 300 m at p90 and 400 m
maximum. There were 147 large-gap transects across 85 segments. This is a material coastal-alignment
limitation; the pipeline does not fill or extrapolate it back to the coast.

All 174 segments have feature rows and 151 have at least one valid transect. Screening counts are 129
`background_only` and 45 `insufficient`; none qualifies as local morphology. Mean valid shares at
100/250/500/1,000 m were 0.140/0.575/0.676/0.663. Median segment depth proxies were 14.19, 16.23, 17.99
and 23.55 m, but 100 m values exist for only 59 segments and are below native resolution.

For supported 250–1,000 m gradients, 120 segments have values: p10 0.00017, p50 0.01423 and p90
0.02491. Negative values remain possible because seabed depth need not increase monotonically. Median
approximate distances to 5/10/20/30 m depth were 230/265/690/1,150 m where reached. Nine segments
met the configured broad-shallow-platform proxy.

Across 7,053 valid samples, depth ranges from 0.0007 to 42.20 m (p1 0.52, p50 19.23, p99 35.96).
About 81.8% of samples with an interpolation flag have value 1 and 18.2% have value 0; the source does
not distinguish interpolation from extrapolation. `stdev` exists for only 746 rows. Reference indices
17267, 18564 and 22062 are retained in the audit. The subset lacks the official reference-string
lookup, so the table records numeric IDs and `unknown` type instead of inventing a mapping.

Manual review sets cover all 45 insufficient segments, all 85 segments with a large-gap transect, the
three orientation ambiguities, 22 Phase 1 source-mismatch segments, deterministic valid samples and
steep/low-relief cross-layer categories. No high-resolution-source segments exist. The combined
Phase 1/2 table is for descriptive plausibility QA only, never an opportunity score.

## QA and reproducibility

Automated QA checks stable unique IDs, 1,200 m lengths, ambiguity exclusion, non-negative canonical
depth, preserved nodata, ordered percentiles, bounded shares/contours and valid screening classes.
Visual QA covers raster/transect coverage, first-valid distance, 250/500/1,000 m depths, gradient,
interpolation share and representative cross-sections. Plots state source, release, native resolution,
datum and regional-proxy safety limits.

Synthetic tests verify both sign conventions, zero/nodata behavior, resolution classes, transects,
known gradient/contour results, provider methods, atomic download/reuse, full output/manifest creation,
reproducible features, cache reuse and stale Phase 1 rejection. The full suite passes 69 tests.

## Outputs

- `data/processed/mallorca_northwest_pilot/bathymetry_transects.parquet`
- `data/processed/mallorca_northwest_pilot/bathymetry_features.parquet`
- `data/processed/mallorca_northwest_pilot/segment_features_phase2.parquet`
- `data/processed/mallorca_northwest_pilot/bathymetry_source_reference_audit.parquet`
- `data/interim/mallorca_northwest_pilot/bathymetry_samples.parquet`
- `outputs/qa/mallorca_northwest_pilot/bathymetry/`
- `outputs/manifests/mallorca_northwest_pilot/*_bathymetry.json`

Raw, interim, processed and run-output data are Git-ignored. The repository stores the exact acquisition
plan/checksum, code, fixtures and documentation required to reproduce them without fabricating or
redistributing Mallorca source data.
