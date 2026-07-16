from pathlib import Path

from streamlit.testing.v1 import AppTest

from coastscan.viewer.launcher import build_streamlit_command

APP_PATH = Path(__file__).parents[1] / "apps" / "coastscan_viewer" / "app.py"
QUALITY_PATH = APP_PATH.parent / "pages" / "2_Data_Quality.py"
METHODOLOGY_PATH = APP_PATH.parent / "pages" / "3_Methodology.py"


def _configure(monkeypatch, root: Path, region: str) -> None:
    monkeypatch.setenv("COASTSCAN_VIEWER_ROOT", str(root))
    monkeypatch.setenv("COASTSCAN_VIEWER_REGION", region)
    monkeypatch.delenv("MAPBOX_API_KEY", raising=False)


def test_application_starts_controls_map_and_filters(viewer_project: Path, monkeypatch) -> None:
    _configure(monkeypatch, viewer_project, "viewer_demo")
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    assert not app.exception
    assert app.title[0].value == "CoastScan"
    assert app.subheader[0].value == "Mallorca Northwest Exploration Viewer"
    assert app.selectbox(key="viewer_metric")
    assert app.selectbox(key="viewer_basemap").value == "CARTO Light"
    assert app.get("deck_gl_json_chart")
    visible = next(metric for metric in app.metric if metric.label == "Visible")
    assert visible.value == "12"

    app.text_input(key="filter_search").input("segment_00").run()
    assert not app.exception
    visible = next(metric for metric in app.metric if metric.label == "Visible")
    assert visible.value == "1"


def test_terrain_only_mode_starts_and_disables_bathymetry(
    terrain_only_viewer_project: Path, monkeypatch
) -> None:
    _configure(monkeypatch, terrain_only_viewer_project, "viewer_terrain_only")
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    assert not app.exception
    assert any("Terrain-only mode" in item.value for item in app.info)
    metric_options = app.selectbox(key="viewer_metric").options
    assert not any("Bathymetry" in option for option in metric_options)
    assert app.checkbox(key="viewer_transects").disabled


def test_missing_outputs_show_actionable_error(tmp_path: Path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path, "missing_region")
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()
    assert not app.exception
    assert any("No processed CoastScan outputs" in item.value for item in app.error)
    assert any("build-region" in item.value for item in app.error)


def test_quality_and_methodology_pages_smoke(viewer_project: Path, monkeypatch) -> None:
    _configure(monkeypatch, viewer_project, "viewer_demo")
    quality = AppTest.from_file(QUALITY_PATH, default_timeout=20).run()
    assert not quality.exception
    assert quality.title[0].value == "Data quality and analytical limitations"
    assert quality.get("deck_gl_json_chart")
    methodology = AppTest.from_file(METHODOLOGY_PATH, default_timeout=20).run()
    assert not methodology.exception
    assert methodology.title[0].value == "Methodology and interpretation boundary"
    assert any("Why there is no combined score" in item.value for item in methodology.markdown)


def test_streamlit_command_uses_active_python_and_safe_arguments(tmp_path: Path) -> None:
    command = build_streamlit_command(
        "viewer_demo", host="127.0.0.1", port=8765, no_browser=True, root=tmp_path
    )
    assert command[1:4] == ["-m", "streamlit", "run"]
    assert "--server.headless" in command
    assert command[command.index("--server.headless") + 1] == "true"
    assert command[-3:] == ["--", "--region", "viewer_demo"]
    assert isinstance(command, list)
