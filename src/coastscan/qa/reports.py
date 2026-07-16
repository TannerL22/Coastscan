"""Machine-readable QA summary and concise HTML report."""

import html
import json
from pathlib import Path
from typing import Any

SAFETY_NOTICE = (
    "CoastScan identifies areas for further desktop and field investigation. Terrain data does not "
    "resolve underwater hazards; offshore transects are analytical geometry only. No output is a "
    "recommendation that a location is safe. Exact locations require legal, environmental and "
    "physical site assessment. Tides, waves, erosion, rockfall and sediment movement can change "
    "conditions."
)


def write_qa_summary(summary: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_qa_report(
    path: Path,
    *,
    region_name: str,
    input_files: list[str],
    source_warnings: list[str],
    counts: dict[str, int],
    coastline_length_m: float,
    orientation_counts: dict[str, int],
    terrain_valid_share: float,
    qa_summary: dict[str, Any],
    artifacts: list[str],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    def items(values: list[str]) -> str:
        return "".join(f"<li>{html.escape(value)}</li>" for value in values)

    failed = list(qa_summary.get("failed_checks", []))
    document = f"""<!doctype html><html><head><meta charset=\"utf-8\">
<title>CoastScan Phase 1 QA</title></head>
<body><h1>{html.escape(region_name)} — Phase 1 QA</h1><p><strong>Safety and uncertainty:</strong>
{html.escape(SAFETY_NOTICE)}</p><h2>Inputs</h2><ul>{items(input_files)}</ul>
<h2>Source metadata warnings</h2><ul>{items(source_warnings) or "<li>None</li>"}</ul>
<h2>Processing</h2><p>Counts: {html.escape(json.dumps(counts, sort_keys=True))}</p>
<p>Clean coastline length: {coastline_length_m:.3f} m</p>
<p>Orientation: {html.escape(json.dumps(orientation_counts, sort_keys=True))}</p>
<p>Mean terrain valid-sample share: {terrain_valid_share:.4f}</p>
<h2>QA</h2><p>Overall pass: {qa_summary.get("passed")}</p>
<p>Failed checks: {html.escape(", ".join(failed) or "None")}</p>
<h2>Artefacts</h2><ul>{items(artifacts)}</ul></body></html>"""
    path.write_text(document, encoding="utf-8")
    return path
