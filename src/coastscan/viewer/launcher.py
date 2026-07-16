"""Cross-platform Streamlit command construction and startup."""

import subprocess
import sys
from pathlib import Path

from coastscan.config import PROJECT_ROOT, load_region_config
from coastscan.exceptions import ConfigurationError, ViewerError


def streamlit_app_path(root: Path = PROJECT_ROOT) -> Path:
    return root / "apps" / "coastscan_viewer" / "app.py"


def build_streamlit_command(
    region: str,
    *,
    host: str = "localhost",
    port: int = 8501,
    no_browser: bool = False,
    root: Path = PROJECT_ROOT,
) -> list[str]:
    app_path = streamlit_app_path(root)
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true" if no_browser else "false",
        "--browser.gatherUsageStats",
        "false",
        "--",
        "--region",
        region,
    ]


def validate_viewer_launch(region: str, root: Path = PROJECT_ROOT) -> Path:
    try:
        load_region_config(region, root)
    except ConfigurationError as exc:
        raise ViewerError(str(exc)) from exc
    app_path = streamlit_app_path(root)
    if not app_path.is_file():
        raise ViewerError(f"Streamlit viewer application not found: {app_path}")
    processed = root / "data" / "processed" / region
    preferred = processed / "segment_features_phase2.parquet"
    phase1 = processed / "segment_features.parquet"
    if not preferred.is_file() and not phase1.is_file():
        raise ViewerError(
            f"No processed CoastScan outputs were found for:\n{region}\n\n"
            "Run:\n\n"
            f"uv run coastscan acquire-region-data --region {region}\n"
            f"uv run coastscan build-region --region {region} --write-samples\n"
            f"uv run coastscan build-bathymetry --region {region} --write-samples"
        )
    return app_path


def launch_viewer(
    region: str,
    *,
    host: str = "localhost",
    port: int = 8501,
    no_browser: bool = False,
    root: Path = PROJECT_ROOT,
) -> int:
    validate_viewer_launch(region, root)
    command = build_streamlit_command(
        region, host=host, port=port, no_browser=no_browser, root=root
    )
    try:
        completed = subprocess.run(command, check=False)
    except FileNotFoundError as exc:
        raise ViewerError(
            "Streamlit is unavailable in the active Python environment. Run: uv sync --python 3.12"
        ) from exc
    if completed.returncode not in {0, 130}:
        raise ViewerError(f"Streamlit viewer exited with status {completed.returncode}")
    return completed.returncode
