"""Region configuration discovery and validation."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from coastscan.exceptions import ConfigurationError
from coastscan.models.region import RegionConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_config_path(region: str | Path, root: Path = PROJECT_ROOT) -> Path:
    """Resolve either a YAML path or a region identifier."""
    candidate = Path(region)
    if candidate.suffix.lower() in {".yml", ".yaml"}:
        return candidate if candidate.is_absolute() else root / candidate
    return root / "config" / "regions" / f"{candidate}.yml"


def load_region_config(region: str | Path, root: Path = PROJECT_ROOT) -> tuple[RegionConfig, Path]:
    """Load a validated region configuration with concise errors."""
    path = resolve_config_path(region, root)
    if not path.is_file():
        raise ConfigurationError(f"Region configuration not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = RegionConfig.model_validate(raw)
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        if isinstance(exc, ValidationError):
            details = "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()
            )
        else:
            details = str(exc)
        raise ConfigurationError(f"Invalid region configuration {path}: {details}") from exc
    return config, path


def data_path(path: Path, root: Path = PROJECT_ROOT) -> Path:
    """Resolve a configured data path relative to project root."""
    return path if path.is_absolute() else root / path
