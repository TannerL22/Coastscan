# Mallorca Phase 1.5 data and validation

## Pilot definition

The bounded `mallorca_northwest_pilot` covers EPSG:4326 bounds
`[2.66, 39.77, 2.80, 39.86]`, from the Port de Sóller area through Cala Tuent to Sa Calobra.
The bounds use named geographic endpoints and documented margins; they are not a box selected after
seeing pipeline results. The official selected natural high-water geometry is approximately 29.10 km long.
It contains multiple bays and headlands, exposed Serra de Tramuntana slopes, locally gentler harbour
terrain, source-geometry mismatch, coastal DEM nodata, and a DEM tile boundary while remaining
practical at 2 m resolution. The generated AOI is
`data/raw/boundaries/mallorca_northwest_aoi.gpkg`; its reproducible metadata is in
`config/aoi/mallorca_northwest_pilot.json`.

## Official products

### Analysis coastline

- Provider: Instituto Hidrográfico de la Marina (IHM), distributed by CNIG.
- Product: `Línea de costa`, `LC.zip`; internal documentation identifies LC2022 and the catalogue
  supplies the attribution label LC 2023. Catalogue publication date: 2022-10-14; source CRS:
  EPSG:4326.
- File: `data/raw/coastline/LC/COSTA/COSTA.shp`.
- Archive SHA-256: `7393e14ca120596d607e68fc0be69e734414a658c447b2f304dd7d5c2ffb11a6`.
- Source fields inspected: `FEATURE`, `LOCALID`, `CATEGORIA`, `ESCALA`, `BAJAMAR`, `PLEAMAR`,
  `CIERRACOST`, `CUBREDESCU`, and `DATE`.
- Analytical selection: `CIERRACOST=true AND PLEAMAR=true` and `CATEGORIA` in `COALNE`,
  `COSTA_ESCARPADA`, or `ORILLA_ARENA`. The official product documentation says the first two fields
  build the high-water closure line; `CIERRACOST=true AND BAJAMAR=true` denotes the low-water line.
  Visual QA of the first real run confirmed that `MUELLE`, `ROMPEOLAS`, `SLCONS`, and `VARADERO`
  represent engineered harbour/constructed features in this pilot, not the target terrestrial rocky
  coastline. Rejected classes and near-coincident representations remain in
  `coastline_source_audit.parquet`; they are not double-counted.
- Required attribution: `Obra derivada de LC 2023 CC-BY 4.0 armada.mde.es/ihm/`.

### Land-orientation mask

- Provider: Instituto Geográfico Nacional / CNIG.
- Product: `Límites municipales, provinciales y autonómicos`, BDLJE edition 2026-02-12,
  1:25,000, EPSG:4258.
- File: `data/raw/boundaries/LINEAS_LIMITE/SHP_ETRS89/` plus
  `recintos_municipales_inspire_peninbal_etrs89/recintos_municipales_inspire_peninbal_etrs89.shp`.
- Archive SHA-256: `d2c5ee140e7f48b3a5fc177b7c2bb05b757472e349290d0d0065d9c562f891da`.
- Selection uses the explicit Mallorca municipality `NATCODE` values in the region YAML. Exact codes
  avoid selecting Menorca, Ibiza, Formentera, or unrelated Balearic islands. Six selected municipal
  records intersect the buffered AOI: Bunyola, Alaró, Deià, Sóller, Fornalutx, and Escorca.
- Role: orientation, land/sea QA, and coarse clipping only. Its seaward boundary never becomes the
  analysis coastline.
- Required attribution: `Obra derivada de BDLJE CC-BY 4.0 ign.es`.

### Elevation

- Provider: Instituto Geográfico Nacional / CNIG.
- Product: MDT02 second coverage (PNOA-LiDAR 2015–2021), Mallorca tiles dated 2019, 2 m COG,
  EPSG:25831, orthometric metres.
- Required tiles and SHA-256 values:
  - `MDT02-ETRS89-HU31-0643-4-COB2.TIF` —
    `c81d1f23cd6094d63810852552d109ec22f34593fa491aa643a8384a6cc56e2e`.
  - `MDT02-ETRS89-HU31-0670-2-COB2.TIF` —
    `736ae763777cd3a1162632bf4076619d15b193e9b47e2f69f9b59f7676119db3`.
- Required attribution: `Obra derivada de MDT02-cob2 2015-2021 CC-BY 4.0 ign.es`.
- CNIG notes that water surfaces can be interpolated to low-reliability constant elevations. The
  pipeline samples only inland transects; neither the raster nor offshore transects represent depth.

## Acquisition

Run:

```powershell
uv run coastscan acquire-region-data --region mallorca_northwest_pilot
```

The command reads `config/acquisitions/mallorca_northwest_pilot.json`, discovers the documented CNIG
resources through the public catalogue workflow, writes through `.part` files, validates checksums
and ZIP/raster integrity, extracts archives without path traversal, preserves original downloads,
and writes `data_catalog/acquisitions/mallorca_northwest_pilot.json`. Matching complete files are
reused. If automated access becomes unavailable, manually download the two named archives and two
named MDT02 tiles from the official references in that plan, place them at its
`local_relative_path` values, and rerun acquisition for validation. Do not substitute a global DEM,
OpenStreetMap, an administrative coast, or fabricated geometry.

## Build and outputs

Inspect headers and vector schemas before the expensive build:

```powershell
uv run coastscan inspect-inputs --region mallorca_northwest_pilot
uv run coastscan build-region --region mallorca_northwest_pilot --force --write-samples
uv run coastscan build-region --region mallorca_northwest_pilot --write-samples
```

The first build creates a clipped windowed virtual-mosaic equivalent, cached DEM, slope, and
roughness rasters. The second validates cache reuse. Analytical outputs are GeoParquet/Parquet under
`data/processed/mallorca_northwest_pilot`; source audits are under `data/interim`; critical JSON,
maps, HTML, logs, and manifests are under `outputs`.

## Known mismatches and review boundaries

The IHM line and municipal mask are independently generalized sources. Boundary distances can reach
tens of metres, so orientation uses several normal origins and fallback distances and preserves
ambiguous or mismatch flags. Terrain origins use the exact coastline pixel when valid or the nearest
valid inland sample within the configured distance; shift distance and quality are recorded. No
origin is silently set to zero. AOI-edge components, harbour structures, river closures, low-water
duplicates, extreme slopes, zero relief, incomplete terrain, and segments near source-tile edges are
reported for visual review.

The generated report states the safety boundary explicitly: this is terrestrial coastal morphology,
not underwater safety evidence. A steep land segment is not evidence of a safe jump, exact coastal
conditions can differ from these source geometries, and legal, environmental, and physical site
assessment remains necessary.

## Future improvements

Before expanding spatial coverage, review flagged source classes and mismatch clusters against newer
official editions, add a more formal coastline-connectivity classifier that distinguishes AOI cuts
from data gaps, and validate coastal MDT02 nodata handling against independent ground control. Any
future bathymetry integration must remain a separate source and uncertainty model. Phase 1.5 does not
implement bathymetry, scoring, recommendations, or Phase 2 modules.
