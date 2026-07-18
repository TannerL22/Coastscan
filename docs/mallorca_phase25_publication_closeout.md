# Mallorca Phase 2.5 publication close-out

Date: 2026-07-18

## Viewer acceptance

The local viewer was launched from the locked Python 3.12 environment with:

```powershell
uv sync --python 3.12
uv run coastscan inspect-viewer-geometry --region mallorca_northwest_pilot
uv run coastscan view-map --region mallorca_northwest_pilot
```

Browser inspection confirmed that the map opens at the northwest Mallorca pilot extent; the 174
coastline segments render as short `PathLayer` lines; no discs, fans or filled polygon artefacts are
present; terrain and bathymetry metrics render; the searchable segment selector opens the selected
segment panel; the optional selected-segment bathymetry transects load offshore; and overlays do not
change the camera extent. The Data Quality map independently renders the same northwest Mallorca
coastline and weakness overlays in the correct spatial position.

The hosted viewer at <https://coastscan.streamlit.app/> was woken from inactivity and inspected in a
browser. It serves the repaired Phase 2.5.1 line-rendering implementation and the 174-row Mallorca
snapshot at the correct northwest extent. Hosted deployment status on 2026-07-18: current and
operational.

## Snapshot provenance and redistribution

`data_catalog/published_snapshots/mallorca_northwest_viewer.json` records all nine committed viewer
files, sizes, SHA-256 checksums, upstream manifests, source releases, required attribution and the
redistribution decision. Official provider terms were reviewed:

- IHM/CNIG: the IHM legal notice applies the Orden FOM/2807/2015 geographic-information licence,
  compatible with CC BY 4.0, to products and derivatives, with mandatory attribution.
- IGN/CNIG: the CNIG data policy permits free lawful use, including derivatives, under a licence
  compatible with CC BY 4.0, with mandatory recognition of IGN origin and ownership.
- EMODnet: EMODnet-created data products are EU-owned and licensed under CC BY 4.0 unless indicated
  otherwise; no contrary restriction is attached to the DTM 2024 product.

The publication is approved for this compact derived snapshot. Raw IHM, CNIG and EMODnet source data
remain excluded. The source-reference audit contains derived cell-reference summaries, not source
survey data. The project-authored AOI rectangle is also included.

Validate the published files with:

```powershell
uv run coastscan validate-published-snapshot --snapshot mallorca_northwest_viewer
```

The 2026-07-18 validation checked nine files (867,266 bytes) and passed.
