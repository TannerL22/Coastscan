# Mallorca northwest Phase 2 bathymetry source assessment

Assessment date: 2026-07-16. AOI: the verified Mallorca northwest Phase 1 pilot,
approximately 2.66–2.80° E and 39.77–39.86° N, plus a 1.2 km offshore corridor.

## Decision

Use the official **EMODnet Digital Bathymetry DTM 2024, Lowest Astronomical Tide
(LAT)** grid as the mandatory regional baseline. The release was published on
2025-03-24, has DOI `10.12770/cf51df64-56f9-4a99-b1aa-36b8d7b743a1`, and a native
spacing of 1/16 arc-minute (about 115 m at this latitude). The pipeline acquires a
geographic subset through the official EMODnet ERDDAP service and preserves the raw
NetCDF unchanged.

This is a regional screening source, not a site-scale water-depth or navigation
source. The official product warning explicitly excludes navigation and safety-at-sea
use. CoastScan preserves that safety boundary.

## Candidates assessed

| Candidate | Access and coverage finding | Resolution / datum | Decision |
|---|---|---|---|
| EMODnet DTM 2024 | Official ERDDAP, WCS, WMS and WFS services; complete western Mediterranean coverage | 1/16 arc-minute (~115 m), LAT selected; MSL also published | Selected mandatory baseline |
| EMODnet HR-DTM catalogue | Official `hr_bathymetry_area` WFS query returned zero AOI intersections | Catalogue products range from 1/32 to 1/512 arc-minute | No public HR overlay available |
| SeaDataNet `DTM-MALLORCA-2018` | Public metadata, data supplied by request; described depth range 100–850 m on the Mallorca continental slope | 50 m horizontal, 0.1 m vertical, MSL | Rejected for this nearshore pilot and not mixed with LAT |
| Spanish Hydrographic Office / IHM | IHM contributes source surveys and composite DTMs to EMODnet; no separate legitimately downloadable higher-resolution analytical raster was found for the AOI | Product-dependent | Use only through documented EMODnet provenance |
| Separate GEBCO global grid | EMODnet source-reference inspection shows GEBCO 2024 fallback in the pilot | Coarser global background | Do not add separately; retain and expose EMODnet fallback provenance |
| Satellite-derived bathymetry | No independently downloadable authoritative SDB covering the AOI was found; EMODnet may incorporate SDB where its source references say so | Product-dependent | Do not generate or add SDB in Phase 2 |

## Inspected EMODnet schema

The selected NetCDF exposes `elevation`, `elevation_min`, `elevation_max`, `stdev`,
`value_count`, `cdi_index`, and `interpolation_flag`. `elevation` is explicitly stored
as negative elevation below LAT. CoastScan maps it to positive-down depth. Under sign
inversion, source `elevation_max` becomes canonical minimum depth and source
`elevation_min` becomes canonical maximum depth. Positive topographic cells and nodata
remain missing; zero is retained as a valid shoreline-level value.

`cdi_index` is retained as the source-reference identifier. It is not silently decoded
without the official lookup. `interpolation_flag=1` means interpolated or extrapolated
because soundings were absent; the source does not distinguish those two cases in this
grid. Observation count and standard deviation are retained only where supplied.

Official source-reference and quality-index WFS layers were inspected for the AOI and
release 2024. Spot checks across the Phase 1 corridor returned GEBCO 2024 background
for most of the pilot, with a coastal-height support layer at the eastern edge. Quality
attributes are sparse or absent for those fallback cells. Accordingly the real pilot's
screening classification is capped at `background_only`; this is an evidence-based
usability limit, not a fabricated quality score.

## Licence and attribution

EMODnet data products are licensed CC BY 4.0. Required acknowledgement:

> This data product was created by EMODnet (https://emodnet.ec.europa.eu/en/), and is
> owned by the EU and licensed under the Creative Commons Attribution 4.0 International
> (CC BY 4.0) license.

## Authoritative references

- EMODnet DTM 2024 release: <https://emodnet.ec.europa.eu/en/emodnet-bathymetry-dtm-2024-release>
- Product overview: <https://emodnet.ec.europa.eu/en/bathymetry>
- ERDDAP dataset metadata: <https://erddap.emodnet.eu/erddap/info/bathymetry_dtm_2024/index.html>
- Web-service documentation: <https://emodnet.ec.europa.eu/en/emodnet-web-service-documentation>
- Terms and attribution: <https://emodnet.ec.europa.eu/en/terms-use-emodnet-online-services-data-and-data-products>
- SeaDataNet candidate metadata: <https://cdi.seadatanet.org/report/3305147>
- IHM EMODnet participation: <https://armada.defensa.gob.es/ihm/Aplicaciones/EMODNET/emodnet.html>

