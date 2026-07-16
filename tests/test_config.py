from pathlib import Path

import pytest

from coastscan.config import load_region_config
from coastscan.exceptions import ConfigurationError


def test_valid_config_loads(synthetic_project: Path) -> None:
    config, _ = load_region_config("demo", synthetic_project)
    assert config.region_id == "demo"
    assert config.analysis_crs == "EPSG:3857"


@pytest.mark.parametrize(
    ("replacement", "field"),
    [
        ("region_name: Synthetic Demo", "region_name"),
        ("analysis_crs: EPSG:3857", "analysis_crs"),
        ("spacing_m: 25", "spacing_m"),
    ],
)
def test_invalid_configuration_is_actionable(
    synthetic_project: Path, replacement: str, field: str
) -> None:
    path = synthetic_project / "config" / "regions" / "demo.yml"
    text = path.read_text(encoding="utf-8")
    if field == "region_name":
        text = text.replace(replacement + "\n", "")
    elif field == "analysis_crs":
        text = text.replace(replacement, "analysis_crs: NOT_A_CRS")
    else:
        text = text.replace(replacement, "spacing_m: -1")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=field):
        load_region_config("demo", synthetic_project)


def test_unsupported_vector_type_fails(synthetic_project: Path) -> None:
    path = synthetic_project / "config" / "regions" / "demo.yml"
    path.write_text(path.read_text().replace("land.geojson", "land.txt"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="GDAL-readable"):
        load_region_config("demo", synthetic_project)
