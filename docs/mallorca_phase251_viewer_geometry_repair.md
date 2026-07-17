# Mallorca Phase 2.5.1 viewer geometry repair

## Original symptom and reproduction boundary

The Phase 2.5 viewer launched successfully but the user's real browser displayed large fan/triangle-like
shapes over a western-Mediterranean extent instead of short northwest Mallorca coastline lines. The
pre-repair application was launched unchanged with:

```powershell
uv run coastscan view-map --region mallorca_northwest_pilot --host 127.0.0.1 --port 8501 --no-browser
```

The live health endpoint returned HTTP 200 and a real-data Streamlit AppTest created one map for 174
visible segments without an application exception. The pre-repair map payload was saved under ignored
`outputs/qa/` diagnostics. It contained 174 GeoJSON LineString features, `filled=false`, WGS84 bounds
`[2.6600000046, 39.7710083264, 2.8000000221, 39.8565837000]` and initial view
`(2.7300000133, 39.8137960132, zoom 10.3283541844)`.

The in-app browser backend was unavailable in the repair environment. Consequently the malformed
browser pixels could not be independently captured, and no authentic `viewer_before_repair.png` was
created. The user's screenshot establishes the visible symptom; the independent reproduction here
establishes the exact live server and payload sent to the browser. These are intentionally not
presented as equivalent evidence.

## Diagnostic findings

The following results were collected before modifying viewer code:

| File | CRS | Active geometry | Rows | Types | WGS84 bounds | Length min / p50 / p90 / max (m) |
| --- | --- | --- | ---: | --- | --- | --- |
| `coast_segments.parquet` | EPSG:25831 | `geometry` | 174 | 174 LineString | 2.660000â€“2.800000, 39.771008â€“39.856584 | 3.191 / 250.000 / 252.045 / 292.688 |
| `segment_features.parquet` | EPSG:25831 | `geometry` | 174 | 174 LineString | identical | identical |
| `segment_features_phase2.parquet` | EPSG:25831 | `geometry` | 174 | 174 LineString | identical | identical |
| `bathymetry_transects.parquet` | EPSG:25831 | `geometry` | 739 | 739 LineString | 2.648790â€“2.813370, 39.768926â€“39.867026 | 1200 / 1200 / 1200 / 1200 |

All four files had a known CRS, no empty or invalid geometry and finite plausible coordinates. Segment
files had no duplicate IDs. `land_test_point` and `sea_test_point` were additional geometry-typed
columns in the segment products. Reprojection used `to_crs`; no CRS was overridden.

Both feature products matched `coast_segments.parquet` for 174/174 IDs and 174/174 exact geometries.
Median and maximum projected Hausdorff distance were 0 m. The Phase 2 merge receives active geometry
from the Phase 1 GeoDataFrame, remains a GeoDataFrame with EPSG:25831, serializes correctly and reads
back with the same active geometry. Row order was not used for these comparisons.

Three sampled Shapely mappings and a five-feature ignored debug GeoJSON had correct LineString nesting
and longitude/latitude order. The complete pre-repair Streamlit chart JSON also retained the same
bounds and declared an unfilled, stroked `GeoJsonLayer`. The existing view-state calculation consumed
the correct bounds. The official raw coastline inspection reported 141 EPSG:4326 LineStrings before
configured filtering, approximately 29.10 km after clipping, and a valid intersecting land mask.

The first representation inconsistent with the observed browser result was the PyDeck layer payload.
PyDeck treats an unquoted Python string as a JavaScript accessor, so the intended deck.gl enum
`width_units="pixels"` was serialized as `widthUnits: "@@=pixels"`, not the literal
`widthUnits: "pixels"`. Deck.gl therefore did not receive a valid unit enum. With rounded caps and
short paths, the resulting widths appeared as the large discs/fans in the browser. No analytical
geometry, CRS, attribute join or Shapely conversion corruption was found.

## Root cause and narrow repair

The defect was incorrect unit serialization at the viewer/rendering boundary. Valid line geometry was
reaching the browser, but the layer width unit was an accessor expression rather than a literal enum.
The first repair changed GeoJSON transport to independent paths but initially retained the same
unquoted-unit defect. Its tests also asserted the broken `"@@=pixels"` payload, creating a false
positive. The user's first post-repair screenshot exposed that gap.

The narrowest defensible repair replaces only line transport/render construction. Every authoritative
LineString is now one PyDeck `PathLayer` record. Each MultiLineString part becomes a separate record
with a stable parent `segment_id` and component index; parts are never concatenated. Colours, tooltip
properties, selection widths and the existing metric registry are unchanged. Flag overlays use the
same authoritative paths as wider unfilled PathLayers. Bathymetry transects remain independent
PathLayer records.

All deck.gl pixel-unit properties now use the quoted PyDeck value `"'pixels'"`; PyDeck strips those
protective quotes and emits the required literal JSON value `"pixels"`. Regression tests inspect the
serialized deck payload, require literal `widthUnits`/`radiusUnits`, and reject `"@@=pixels"` anywhere
in it. The Streamlit chart key is versioned for this contract change so an existing browser session
cannot retain malformed layer or camera state after deployment.

## Authoritative geometry and validation contract

The viewer now always reads map geometry from `coast_segments.parquet`. The preferred Phase 2 table or
terrain-only Phase 1 table supplies attributes only. Both inputs must contain one unique row per
`segment_id` and identical ID sets. Every geometry-typed attribute column is removed before a validated
one-to-one ID merge, and the GeoDataFrame is constructed explicitly with authoritative projected
geometry. Both file checksums participate in the cache key and appear in viewer provenance.

Before rendering, reusable validation requires:

- projected CRS metadata, active non-empty valid LineString/MultiLineString geometry and unique IDs;
- finite coordinates, valid WGS84 ranges, bounded coordinate jumps and defensible line lengths;
- intersection with the configured AOI plus 1.5 km tolerance, aggregate bounds inside that tolerance
  and a nearby aggregate centroid;
- transect LineStrings with unique IDs, known non-ambiguous parents, plausible jumps/lengths and origins
  within 50 m of their authoritative parent segment.

Any failure raises an actionable `ViewerError` before a map is constructed. No `set_crs` repair is used.
The projected analytical frame is retained; display conversion creates a separate EPSG:4326 copy.

Initial view now uses deterministic Web Mercator fit-bounds math for a representative 1200 Ã— 650 map
with padding. The unfiltered pilot centres at longitude `2.7300000133`, latitude `39.8137960132`, zoom
`11.5282992212`. A one-segment filter fitted that segment at zoom 16. Empty and constant-span cases are
handled explicitly.

## Files changed

- `src/coastscan/viewer/data.py`, `models.py`, `validation.py`, `diagnostics.py`, `layers.py`
- `src/coastscan/cli.py`
- synthetic viewer fixture generator, authoritative coastline fixtures and viewer tests
- `README.md`, `apps/coastscan_viewer/README.md`, `docs/local_viewer.md` and this report

No Phase 1 or Phase 2 implementation, input or analytical output was modified.

## Real Mallorca application evidence

The repaired real-data AppTest produced no exceptions and reported:

- 174 independent coastline PathLayer records representing 174 segments;
- exact display bounds `2.6600000046â€“2.8000000221 / 39.7710083264â€“39.8565837000`;
- 121 default continuous colours and 126 colours for `slope_p90_deg`;
- three categorical colours for `orientation_status`;
- selected-segment highlighting and three unchanged detail tabs;
- six independent transects for the selected test segment, all with only that parent ID;
- three ambiguous-segment overlay paths, all unfilled PathLayer records;
- a one-segment filter with local fitted bounds;
- only PathLayers on the Data Quality map, with the same 174-segment bounds.

The real diagnostic command is:

```powershell
uv run coastscan inspect-viewer-geometry --region mallorca_northwest_pilot
```

It reports validation `pass`, 174/174 exact authoritative/attribute geometry matches, zero invalid
coordinates, zero out-of-AOI segments, a maximum segment coordinate jump of 134.018 m and 739 validated
independent transects.

The in-app browser backend remained unavailable, so the final visual gate was completed with the
user's real browser. A screenshot after a clean server restart shows the 174 coloured short paths
following northwest Mallorca's coastline at a useful local zoom, with no large discs or polygons.
The preceding screenshot was traced to a Streamlit process started before the unit fix; the exact stale
process tree was stopped, port 8501 was verified clear and the repaired server was started afresh before
the successful screenshot. No screenshot file is committed to the repository. Searchable-ID selection,
filtering, selection styling, tooltip payload content, transects, overlays and the Data Quality payload
were verified programmatically.

## Tests and limitations

New tests cover authoritative geometry precedence, reordered and deliberately corrupt attribute
geometry, one-to-one ID errors, duplicates, missing/incorrect CRS, non-mutating reprojection, AOI and
world-coordinate bounds, coordinate jumps, LineString/MultiLineString path independence, selection
styling, literal pixel-unit serialization, rejection of accessor-style unit values, unfilled rendering,
flag paths, fit bounds, transects and actionable Streamlit errors. The final suite passed 108 tests
with no failures or skips and 16 pre-existing NumPy
masked-array deprecation warnings. Ruff format/lint, MyPy across 62 source files and `uv build` passed.

Protected analytical SHA-256 values were identical before and after:

| File | Before and after SHA-256 |
| --- | --- |
| `coast_segments.parquet` | `DC6D795E5EC68D5360ECA0F48CDC1902B153A765756737CA38EA12F89C88ADD1` |
| `segment_features.parquet` | `6C4974AB1B2991A9EFAA9E92C570AC4A8F1D85B8631959767765E130D53C02F0` |
| `bathymetry_transects.parquet` | `1AD89D457E71021EBB96DCBF655B136B0B3F181569166E1373ACA2FE53D1BE48` |
| `bathymetry_features.parquet` | `3215C9A2E20AFDC23A83756F8B2E307D4D8BA0A346FFD520B7CC192C75CEB6CF` |
| `segment_features_phase2.parquet` | `55CF0DF311EFCBF74EA2E44CCAA37FC385DAA50F0E57D3E4CE66EBE8C9F8662A` |

Known limitations remain: map tiles require network access, fit bounds uses a representative rather
than measured browser viewport, PyDeck map-click selection is browser-integration dependent and the
viewer provides no vector-tile/offline packaging. The ID selector and table remain reliable selection
fallbacks. No metric, filter, score, analytical feature, data layer, page or deployment feature was
added.
