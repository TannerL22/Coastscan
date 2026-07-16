# Mallorca Phase 2.5 local viewer verification

Verification date: 2026-07-16. Starting commit: `bd6988f`.

## Live startup

The CLI launcher started the real viewer through the active Python environment at
`http://127.0.0.1:8501` with `--no-browser`. The Streamlit health endpoint returned HTTP 200 and the
server was shut down after inspection. The first attempted background launch lacked the repository
`UV_CACHE_DIR`; relaunching with the same local cache used by verification resolved that environment
issue without an application change.

## Real Mallorca interaction review

The viewer loaded `segment_features_phase2.parquet` (174 segments) and lazily loaded
`bathymetry_transects.parquet` only after the layer toggle. The real app was exercised using
Streamlit's supported application runtime/testing interface while the local server startup was checked
separately. The in-app pointer-control surface was unavailable in this session, so no screenshot or
pixel-comparison claim is made.

Verified interactions:

- Default terrain relief map: 174 visible segments, one PyDeck map, CARTO Light.
- Terrain metric: terrestrial slope p90 rendered without an exception.
- Bathymetry metric: 500 m regional depth proxy rendered without an exception.
- Categorical metric: bathymetry screening class rendered without an exception.
- Screening filter: `background_only` reduced the visible set to 129 segments.
- Segment selection: a stable Mallorca segment ID produced three profile tabs and deterministic text.
- Transect toggle: selected-segment bathymetry transects were added to the map.
- Flag overlay: ambiguity rendered; the synthetic suite separately exercises every overlay type.
- Empty search: the app displayed the reset/broaden-filters message without a traceback.
- Data-quality page: 174 segments, three ambiguous orientations, 22 source mismatches and a map.
- Methodology page: interpretation boundary and explicit no-combined-score explanation rendered.
- Terrain-only synthetic mode: bathymetry controls and transects were disabled with a build instruction.
- Missing-output mode: actionable acquisition/Phase 1/Phase 2 commands appeared without a traceback.

## Analytical regression

The protected hashes before and after implementation are identical:

| File | SHA-256 |
|---|---|
| `coast_segments.parquet` | `DC6D795E5EC68D5360ECA0F48CDC1902B153A765756737CA38EA12F89C88ADD1` |
| `segment_features.parquet` | `6C4974AB1B2991A9EFAA9E92C570AC4A8F1D85B8631959767765E130D53C02F0` |
| `bathymetry_transects.parquet` | `1AD89D457E71021EBB96DCBF655B136B0B3F181569166E1373ACA2FE53D1BE48` |
| `bathymetry_features.parquet` | `3215C9A2E20AFDC23A83756F8B2E307D4D8BA0A346FFD520B7CC192C75CEB6CF` |
| `segment_features_phase2.parquet` | `55CF0DF311EFCBF74EA2E44CCAA37FC385DAA50F0E57D3E4CE66EBE8C9F8662A` |

## Automated verification

`uv sync --python 3.12`, Ruff formatting/lint, MyPy across 60 source files and all 95 tests passed.
Required synthetic and Mallorca Phase 1/2 inspection/build commands also completed successfully. The
test suite emitted 16 existing NumPy masked-array deprecation warnings and no failures or skips.

