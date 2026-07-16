# CoastScan local interactive viewer

## Scope

Phase 2.5 is a local Streamlit application for exploring verified Phase 1 terrain and Phase 2 regional
bathymetry outputs. It reads analytical products but never rebuilds features, modifies GeoParquet files,
or creates a combined ranking. The application is intended for regional desktop investigation only.

## Architecture

Presentation files live under `apps/coastscan_viewer/`:

- `app.py` — main map, filters, selection, profile and CSV export.
- `pages/2_Data_Quality.py` — distributions, provenance and weakness-focused mapping.
- `pages/3_Methodology.py` — concise processing explanation and interpretation boundary.

Testable viewer logic lives under `src/coastscan/viewer/`:

- `data.py` discovers, validates, hashes, caches and reprojects inputs.
- `metrics.py` owns every map label, unit, description, format and interpretation boundary.
- `filters.py` applies non-mutating geometry, terrain, bathymetry and search filters.
- `layers.py` creates colour scales and PyDeck segment/transect/flag/midpoint layers.
- `formatting.py` formats values, creates deterministic interpretation text and CSV exports.
- `selection.py` parses supported Streamlit PyDeck/table selection events.
- `launcher.py` constructs and starts the cross-platform Streamlit command.

## Inputs and immutable display transformation

The preferred input is:

```text
data/processed/<region>/segment_features_phase2.parquet
```

The viewer also uses `bathymetry_transects.parquet` when requested. If the joined Phase 2 table is
absent, `segment_features.parquet` enables terrain-only mode. GeoParquet CRS metadata is mandatory.
The projected analytical frame is retained in memory, while a separate display frame is transformed to
EPSG:4326 for deck.gl. Source files are never written or simplified.

`st.cache_data` keys include the absolute path, file size, nanosecond modification time and SHA-256.
Transects are loaded lazily and normally filtered to the selected segment. This is appropriate for the
174-segment pilot and remains practical for several thousand segments without adding a database or tile
server.

## Metrics and colour scales

The central registry includes terrestrial relief, slope, steepness, roughness and completeness;
regional target-distance depths, seabed gradients, approximate contour distances, first-valid-water
gaps, source shares and completeness; plus orientation and quality categories. Fields absent from the
loaded table are omitted from controls.

Continuous colours use visible valid values only. Robust mode uses 5th–95th percentiles; full mode uses
the observed range. Constant and all-missing fields are handled explicitly, and missing segments are
grey. Gradients use a zero-centred diverging scale. Palettes intentionally avoid automatic red/green
site-level semantics. The active numeric range and unit are displayed below the controls.

## Filters

Filters cover orientation, terrain/bathymetry availability, source mismatch, segment ID, relief, slope,
steep-nearshore share, terrain completeness, bathymetry screening class, bathymetry completeness,
first-valid-water distance, target-distance depth, regional gradient and global-fallback share where
available. Filters operate only on the cached display copy and update counts and colour scales. One
button resets every filter widget.

## Layers and selection

The main `GeoJsonLayer` preserves stable `segment_id`, exposes a concise tooltip and supports
Streamlit's documented `st.pydeck_chart(..., on_select="rerun")` selection state. A searchable segment
selector and a selectable filtered table provide reliable alternatives. The selected segment is
emphasized and receives identity/provenance, terrain, bathymetry and deterministic interpretation
panels.

Dedicated `PathLayer` bathymetry transects are off by default and can show the selected segment or all
visible segments. Flag overlays cover ambiguity, source mismatch, large coastal gaps, missing terrain,
missing bathymetry and fallback/background bathymetry. Optional midpoint markers make narrow segments
easier to locate.

Filtered CSV export excludes geometry and prefixes regional proxy fields with `regional_proxy__`.

## Basemaps and secrets

CARTO Light and CARTO Dark are the defaults and require no token. If a local `MAPBOX_API_KEY` exists,
the application adds a satellite option and passes the token directly to PyDeck. It is never displayed,
logged, written into output data or committed. `.streamlit/secrets.toml`, Streamlit cache data and `.env`
are ignored.

## Launch

```powershell
uv run coastscan view-map --region mallorca_northwest_pilot
```

Defaults are `localhost:8501`. Options include `--host`, `--port`, `--no-browser` and `--verbose`.
Direct execution is supported:

```powershell
uv run streamlit run apps/coastscan_viewer/app.py -- --region mallorca_northwest_pilot
```

The launcher invokes `sys.executable -m streamlit` with an argument list and no shell-specific quoting.

## Testing

The committed synthetic fixture represents high/low relief, resolved/fallback/ambiguous orientation,
missing terrain and bathymetry, regional/background screening, a large coastal gap, fallback dominance,
positive/negative gradients, constant roughness and missing values. Tests cover loaders, CRS behavior,
source immutability, metric metadata, filters, scales, layers, selection, interpretation, CLI arguments
and Streamlit AppTest smoke flows. Live verification additionally checks server health and the real
Mallorca controls and pages.

## Known limitations and safety boundary

Streamlit/PyDeck displays line selection but does not provide production vector tiles or offline map
packaging. CARTO and optional satellite tiles require internet access even though the analytical data
remain local. The viewer does not persist sessions outside the local Streamlit process.

Terrain is terrestrial only. Regional bathymetry does not measure water beneath an individual cliff,
EMODnet is not navigation data, coarse cells may contain fallback or interpolated evidence, submerged
obstacles are unresolved and conditions change over time. Exact sites require legal, environmental and
physical assessment. No overall exploration, jumping or safety score exists.

