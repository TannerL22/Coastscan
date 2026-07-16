# CoastScan

CoastScan Phase 1 is a reproducible GIS pipeline that turns a configured regional land polygon
and local elevation raster into cleaned coastline parts, stable approximately 250 m segments,
landward/offshore transects, terrestrial morphology features, run manifests, and QA artefacts.

## Safety and uncertainty

CoastScan identifies areas for further desktop and field investigation. It is not a safety
certification or a recommendation that any location is safe. Terrain data does not resolve water
depth, submerged rocks, ledges, or trajectories. Offshore transects are analytical geometry only.
Exact locations require legal, environmental, and physical site assessment. Conditions can change
with tides, waves, erosion, rockfall, and sediment movement.

## Phase 1 scope

Implemented: configuration validation, polygon input and minimal validity repair, exterior/interior
shoreline classification, conservative cleaning, stable segmentation, local tangent and orientation,
transects, DEM validation/reprojection/cache, nodata-aware slope and local elevation-standard-deviation
roughness, inland sampling, segment features, provenance manifests, QA checks/maps/report, and synthetic
fixtures. Phase 1 does not include bathymetry, water clarity, satellite imagery, geology, waves,
protected areas, access or exploration scoring, machine learning, or a public frontend.

## Install

Python 3.12 and `uv` are required.

```bash
uv sync
uv run coastscan --help
```

PowerShell uses the same commands:

```powershell
uv sync
uv run coastscan inspect-inputs --region mallorca_pilot
```

## Inputs and configuration

Region YAML lives under `config/regions/`. The Mallorca configuration expects:

- `data/raw/boundaries/mallorca_land.gpkg`, layer `land`: valid/repairable Polygon or MultiPolygon,
  with a known CRS. GeoJSON, Shapefile and GeoParquet are also supported when the config path/layer
  are updated.
- `data/raw/elevation/mallorca_dem.tif`: GDAL-readable elevation raster, known CRS, valid affine
  transform, metre vertical units, explicit nodata where applicable, and overlap with Mallorca.

No real Mallorca source data is included or fabricated. Complete `data_catalog/sources.csv` from the
authoritative provider metadata and record checksums before production use.

## Commands

```bash
uv run coastscan inspect-inputs --region mallorca_pilot
uv run coastscan build-region --region mallorca_pilot --write-samples
uv run python scripts/build_region.py --region mallorca_pilot
```

To exercise the explicit synthetic demo:

```bash
uv run python scripts/create_synthetic_fixtures.py
uv run coastscan build-region --region synthetic_demo --force --write-samples
```

`build-region` also accepts `--skip-qa-map` and `--verbose`. Missing mandatory inputs produce a
concise non-zero error. `--force` rebuilds cached terrain; otherwise a source/config/region cache key
prevents redundant reprojection.

## Outputs

- `data/interim/<region>/`: cleaned coastline, cached DEM/slope/roughness and optional long samples.
- `data/processed/<region>/`: segments, transects, terrain features and joined segment features.
- `outputs/manifests/<region>/`: timestamped JSON manifest and run log.
- `outputs/qa/<region>/`: JSON QA summary and static overview/orientation/cross-section/distribution PNGs.
- `outputs/reports/<region>/phase1_qa_report.html`: concise report and safety notice.

Processed vectors remain in the metric analysis CRS. GeoParquet stores CRS metadata. Roughness is the
centred, nodata-aware population standard deviation of elevation in the nearest odd-pixel window to
`roughness_window_m`.

## Development and tests

```bash
uv run ruff check .
uv run pytest
uv run mypy src
```

Equivalent Make targets are `install`, `lint`, `test`, `inspect`, `build`, and `qa`; set `REGION` as
needed. Tests programmatically cover rectangular/curved/narrow/multipart/lake geometries and linear,
nodata, steep, and flat synthetic rasters.

## Known limitations and next work

Orientation is local planar classification and correctly leaves geometrically unresolved cases
ambiguous. DEM vertical units are declared by configuration/catalogue rather than inferred from GeoTIFF
metadata. Terrain preparation currently reprojects and clips in memory, appropriate for the stated pilot
scale but not continental rasters. The next phase should add independently sourced bathymetry and its
uncertainty model; it must not reinterpret Phase 1 offshore transects as depth evidence.
