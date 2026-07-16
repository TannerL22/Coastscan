"""Region and runtime argument helpers shared by Streamlit pages."""

import argparse
import os
import sys
from pathlib import Path

from coastscan.viewer.data import viewer_project_root


def requested_region(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--region")
    parsed, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    return parsed.region or os.environ.get("COASTSCAN_VIEWER_REGION", "mallorca_northwest_pilot")


def available_regions(root: Path | None = None) -> list[str]:
    project_root = (root or viewer_project_root()).resolve()
    region_ids: set[str] = set()
    config_dir = project_root / "config" / "regions"
    if config_dir.is_dir():
        region_ids.update(path.stem for path in config_dir.glob("*.yml"))
        region_ids.update(path.stem for path in config_dir.glob("*.yaml"))
    processed = project_root / "data" / "processed"
    if processed.is_dir():
        region_ids.update(path.name for path in processed.iterdir() if path.is_dir())
    return sorted(region_ids)
