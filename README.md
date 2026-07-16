# CoastScan

CoastScan Phase 1/1.5 is a reproducible GIS pipeline that turns configured coastline, land-mask,
and elevation sources into cleaned coastline parts, stable approximately 250 m segments,
landward/offshore transects, terrestrial morphology features, run manifests, and QA artefacts.

## Safety and uncertainty

CoastScan identifies areas for further desktop and field investigation. It is not a safety
certification or a recommendation that any location is safe. Terrain data does not resolve water
depth, submerged rocks, ledges, or trajectories. Offshore transects are analytical geometry only.
Exact locations require legal, environmental, and physical site assessment. Conditions can change
with tides, waves, erosion, rockfall, and sediment movement.

## Phase 1 scope

Implemented: configuration validation, polygon-derived and direct-line coastline modes, a separate
land-orientation mask, conservative cleaning, stable segmentation, multi-point orientation voting,
transects, windowed multi-tile DEM validation/reprojection/cache, nodata-aware slope and roughness,
nearest-valid-inland terrain origins, segment features, provenance manifests, QA checks/maps/report,
official-data acquisition metadata, and synthetic fixtures. Phase 1 does not include bathymetry,
water clarity, satellite imagery, geology, waves,
protected areas, access or exploration scoring, machine learning, or a public frontend.

## Install

Python 3.12 and `uv` are required.

```bash
uv sync
uv run coastscan --help
```

PowerShell uses the same commands. The verification environment is Python 3.12:

```powershell
uv sync --python 3.12
uv run coastscan inspect-inputs --region synthetic_demo
```

## Coastline and terrain source architecture

Region YAML lives under `config/regions/`. Existing regions can derive the coastline from a land
polygon. The preferred production mode loads an authoritative line/multiline coastline directly;
that geometry controls cleaning, IDs, bearings, origins, and length. A separate polygon is used only
for land/sea orientation, QA, and coarse clipping. The administrative boundary never replaces an
available direct coastline.

Elevation can be one raster, explicit paths, a directory, or a glob. Tile headers are checked for
CRS, resolution, nodata, transform, and vertical-unit consistency. Only processing-corridor tiles and
windows are read; the deterministic first-valid overlap rule feeds a clipped cached raster and
blockwise slope/roughness derivatives. All selected source checksums participate in the cache key.

## Mallorca northwest real pilot

`mallorca_northwest_pilot` covers approximately 29.10 km of official natural high-water coastline from the
Port de Sóller area through Cala Tuent to Sa Calobra. It uses the IHM/CNIG `Línea de costa` COSTA
layer (`CIERRACOST=true`, `PLEAMAR=true`) limited to the natural `COALNE`, `COSTA_ESCARPADA`, and
`ORILLA_ARENA` classes, the CNIG municipal dataset as orientation support, and two
intersecting CNIG MDT02 second-coverage 2 m COG tiles. Raw downloads and generated rasters are ignored
by Git; source metadata and checksums are retained in the acquisition manifest.

Acquire or validate the required official files with:

```bash
uv run coastscan acquire-region-data --region mallorca_northwest_pilot
```

The downloader uses the public CNIG catalogue workflow, validates checksums/archive integrity, uses
safe partial files, and reuses matching downloads. If CNIG requires manual interaction, use the exact
product references and filenames in `config/acquisitions/mallorca_northwest_pilot.json`, place them at
the listed local paths, and rerun the command. It will validate rather than fabricate or substitute
data. Full source details and attribution are in `docs/mallorca_phase1_data.md`.

## Commands

```bash
uv run coastscan inspect-inputs --region mallorca_pilot
uv run coastscan build-region --region mallorca_pilot --write-samples
uv run python scripts/build_region.py --region mallorca_pilot

uv run coastscan inspect-inputs --region mallorca_northwest_pilot
uv run coastscan build-region --region mallorca_northwest_pilot --force --write-samples
```

To exercise the explicit synthetic demo:

```bash
uv run python scripts/create_synthetic_fixtures.py
uv run coastscan build-region --region synthetic_demo --force --write-samples
```

`build-region` also accepts `--skip-qa-map` and `--verbose`. Missing mandatory inputs produce a
concise non-zero error. `--force` rebuilds cached terrain; omit it for a checksum-validated cached
rerun.

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

## Real-data limitations

Orientation is local planar classification and correctly leaves geometrically unresolved cases
ambiguous. The municipal mask and hydrographic coastline differ by tens of metres in places, so flags
and endpoint QA require inspection. MDT02 water surfaces can contain low-reliability interpolated
values, coastal pixels can be nodata, and horizontal mismatch can shift the local terrain origin;
origin shifts are recorded and sea-level zero is never substituted. The bounded pilot is not a
full-island validation. A later phase may add independently sourced bathymetry and its uncertainty
model, but it must never reinterpret Phase 1 offshore transects or MDT02 as depth evidence.
