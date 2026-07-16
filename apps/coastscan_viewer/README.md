# CoastScan local viewer

Launch from the repository root:

```powershell
uv run coastscan view-map --region mallorca_northwest_pilot
```

Direct Streamlit execution is also supported:

```powershell
uv run streamlit run apps/coastscan_viewer/app.py -- --region mallorca_northwest_pilot
```

The viewer reads existing processed GeoParquet outputs and never rebuilds or writes analytical data.
The default CARTO basemap needs no token. A satellite option appears only when `MAPBOX_API_KEY` is
supplied by the local user; never commit `.streamlit/secrets.toml`.

Keep data, filtering, metric and layer logic in `src/coastscan/viewer/`. Page files should remain
presentation-focused. Run `uv run pytest` and inspect the live application before committing UI work.
