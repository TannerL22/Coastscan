# CoastScan

CoastScan is a reproducible GIS pipeline with separate terrestrial and bathymetry stages. Phase 1
turns configured coastline, land-mask and elevation sources into stable coastline segments and
terrestrial morphology. Phase 2 adds resolution-aware regional offshore morphology without changing
the Phase 1 contracts.

## Safety and uncertainty

CoastScan identifies areas for further desktop and field investigation. It is not a safety
certification or a recommendation that any location is safe. Terrain and regional bathymetry do not
resolve current water depth, submerged rocks, ledges or trajectories. The EMODnet product is not
navigation data. Exact locations require legal, environmental and physical site assessment; tides,
waves, erosion, rockfall and sediment movement can change conditions.

## Implemented scope

Phase 1 includes strict configuration, polygon-derived and authoritative direct-line coastline modes,
a separate land-orientation mask, cleaning, stable segmentation, multi-point orientation voting,
transects, windowed multi-tile DEM reprojection/cache, nodata-aware slope and roughness, terrain
origins, segment features, manifests and QA.

Phase 2 is an independent cached bathymetry stage. It consumes stable Phase 1 segments, prepares
canonical positive-down depth rasters, creates separate long offshore transects, finds the first valid
marine cell and calculates target-distance depths, regional gradients, contour-distance proxies,
source/quality summaries and transparent usability classes. It never overwrites Phase 1
`segment_features.parquet`.

Phase 2.5 adds a local Streamlit and PyDeck exploration viewer. Authoritative display geometry comes
from `coast_segments.parquet`; terrain or Phase 2 attributes are joined one-to-one by `segment_id`, and
a separately validated in-memory EPSG:4326 copy becomes independent PyDeck line paths. The viewer
provides transparent terrain, regional bathymetry and data-quality controls without calculating new
analytical features or any combined score.

The bathymetry hierarchy is: public authoritative high-resolution data where it genuinely overlaps;
the latest stable EMODnet regional DTM as the mandatory baseline; lower-quality fallback cells retained
with provenance; and a separate global grid only for a real uncovered gap. No public EMODnet HR-DTM
intersects the Mallorca northwest pilot, so it uses EMODnet DTM 2024 LAT at about 115 m native spacing.
A 100 m target is below native resolution; 250–1,000 m values remain regional proxies.

Out of scope: water clarity, satellite-derived bathymetry generation, geology, waves, protected areas,
access, exploration scoring, machine learning, frontend work and site-safety conclusions.

## Install

Python 3.12 and `uv` are required.

```bash
uv sync --python 3.12
uv run coastscan --help
```

## Mallorca northwest real pilot

`mallorca_northwest_pilot` covers approximately 29.10 km of official natural high-water coastline from
the Port de Sóller area through Cala Tuent to Sa Calobra. Phase 1 uses the IHM/CNIG `Línea de costa`
natural COSTA classes, the CNIG municipal dataset as orientation support and two CNIG MDT02
second-coverage 2 m COG tiles. Phase 2 uses a checksum-pinned official EMODnet DTM 2024 LAT subset.

Acquire or validate the official resources with:

```bash
uv run coastscan acquire-region-data --region mallorca_northwest_pilot
```

The provider-dispatched downloader preserves CNIG catalogue behavior and supports stable official
HTTPS/EMODnet resources. It uses partial files and atomic completion, validates or calculates checksums,
records retrieval metadata and reuses matching local files. It never bypasses approval restrictions.

Source and attribution details are in:

- `docs/mallorca_phase1_data.md`
- `docs/mallorca_phase2_bathymetry_source_assessment.md`
- `docs/mallorca_phase2_bathymetry.md`

## Commands

```bash
# Phase 1 remains backward-compatible
uv run coastscan inspect-inputs --region mallorca_northwest_pilot
uv run coastscan build-region --region mallorca_northwest_pilot --force --write-samples

# Independent Phase 2
uv run coastscan inspect-bathymetry --region mallorca_northwest_pilot
uv run coastscan build-bathymetry --region mallorca_northwest_pilot --force --write-samples
uv run coastscan build-bathymetry --region mallorca_northwest_pilot --write-samples

# Local Phase 2.5 viewer
uv run coastscan inspect-viewer-geometry --region mallorca_northwest_pilot
uv run coastscan view-map --region mallorca_northwest_pilot
```

Both build commands accept `--skip-qa-map` and `--verbose`. `--force` rebuilds only that stage's cache.
Changing bathymetry never requires rerunning the 2 m terrain stage.

The viewer opens at `http://localhost:8501` by default. It always uses
`coast_segments.parquet` as geometry authority, prefers `segment_features_phase2.parquet` for
attributes and lazily loads validated `bathymetry_transects.parquet` only when that layer is enabled.
If Phase 2 is absent, `segment_features.parquet` enables a clearly labelled terrain-only mode and
bathymetry controls are disabled. Missing or spatially invalid inputs stop with actionable errors.

The default CARTO Light/Dark basemaps require no account, API key or secret. A satellite option appears
only when a local `MAPBOX_API_KEY` is supplied; `.env.example` documents the optional variable and
secrets are Git-ignored. See `docs/local_viewer.md` for architecture, filters, testing and limitations.

For the explicit synthetic demo:

```bash
uv run python scripts/create_synthetic_fixtures.py
uv run coastscan build-region --region synthetic_demo --force --write-samples
```

## Outputs

- `data/interim/<region>/`: cached Phase 1 rasters and optional Phase 1/2 samples.
- `data/processed/<region>/coast_segments.parquet`: stable upstream segment contract.
- `data/processed/<region>/segment_features.parquet`: unchanged Phase 1 joined features.
- `data/processed/<region>/bathymetry_transects.parquet`: separate long Phase 2 transects.
- `data/processed/<region>/bathymetry_features.parquet`: regional bathymetry proxies.
- `data/processed/<region>/segment_features_phase2.parquet`: descriptive Phase 1/2 join.
- `outputs/manifests/<region>/`: separate timestamped Phase 1 and Phase 2 manifests.
- `outputs/qa/<region>/`: machine-readable QA and static maps/cross-sections.

Raw, interim, processed and run-output data are Git-ignored. The public repository stores acquisition
plans, expected checksums, code, synthetic fixtures and documentation needed to reproduce official-data
outputs without fabricating or redistributing Mallorca source data.

## Development and tests

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run mypy src
```

The synthetic suite covers coastline geometries and terrestrial rasters plus canonical bathymetry sign
conversion, zero/nodata behavior, resolution classes, separate transects, known gradients/contours,
provider methods, atomic download/reuse, Phase 2 manifests, cache reuse and stale-upstream rejection.
Viewer tests cover authoritative geometry/attribute joins, CRS and AOI validation, immutable
reprojection, independent LineString/MultiLineString path conversion, fit-bounds behavior, transects,
flag overlays, metric metadata, filters, colour scales, selection, terrain-only operation, invalid or
missing files and Streamlit smoke tests.

## Real-data limitations

The municipal orientation mask and hydrographic coastline differ by tens of metres in places. MDT02
coastal pixels can be nodata or interpolated. EMODnet 2024 is a harmonised regional grid and the pilot
is predominantly supported by coarse GEBCO fallback cells; source quality fields are sparse, the
interpolation flag does not distinguish interpolation from extrapolation and many transects have a
coastline-to-first-valid-cell gap. QA retains these limitations, and the real screening class is capped
at `background_only`. Neither stage supplies site-level safety evidence.

The viewer exposes individual measurements and regional proxies for sorting and filtering. It does not
rank the "best" coastline, make trip suggestions, or provide an exploration, jumping or safety score.
