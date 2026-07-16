"""Concise methodology and safety boundary."""

import streamlit as st

st.set_page_config(page_title="CoastScan — Methodology", page_icon="📘", layout="wide")
st.title("Methodology and interpretation boundary")

st.markdown(
    """
### Coastline segmentation

Authoritative coastline geometry is cleaned and divided into stable segments of approximately 250 m.
Segment IDs and the projected analytical geometry come from Phase 1; the viewer only makes an
in-memory EPSG:4326 display copy.

### Landward and seaward orientation

A separate land polygon supports multi-point orientation voting. Resolved fallback and ambiguous
results remain visible. The administrative mask never replaces the authoritative coastline.

### Terrestrial morphology

Dedicated inland transects sample the 2 m terrestrial DEM. Relief describes elevation rise from the
coast-origin area; slope, steep-sample share and roughness are land-only features. They cannot be
continued offshore or treated as underwater evidence.

### Regional bathymetry

Phase 2 creates separate, longer offshore transects. It searches seaward for the first valid marine
cell and records the coastline-to-grid gap. Positive-down depths at configured distances and seabed
gradients are regional proxies aggregated across transects.

### Native resolution

Sampling positions do not create information finer than the source. The Mallorca baseline is roughly
115 m, so its 100 m proxy is below one native cell. Approximate contour distances carry cell-scale
uncertainty.

### Screening classes

`local_morphology_candidate`, `coastal_context`, `regional_screening`, `background_only` and
`insufficient` describe data usability. They are not a ranking of locations. The real pilot is
capped at `background_only` because its bathymetry is predominantly coarse fallback evidence.

### Why there is no combined score

Terrain relief and regional bathymetry can be filtered as separate transparent measurements.
Combining them into an overall ranking would hide resolution, provenance and missing-data
limitations. This phase therefore contains no overall exploration or site-level score.
"""
)

st.warning(
    "Regional bathymetry does not measure the water beneath an individual cliff and cannot resolve "
    "submerged obstacles. EMODnet is not navigation data. Conditions change over time, and exact "
    "sites require legal, environmental and physical assessment."
)

st.markdown(
    """
Repository references:

- `README.md`
- `docs/mallorca_phase1_data.md`
- `docs/mallorca_phase2_bathymetry.md`
- `docs/mallorca_phase2_bathymetry_source_assessment.md`
"""
)
