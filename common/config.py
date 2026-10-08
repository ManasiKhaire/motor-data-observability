"""Settings (config/settings.yaml) and secrets (.env) in one place."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "settings.yaml"


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into os.environ (no extra package needed)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(path: Path = CONFIG_PATH) -> dict:
    load_env()
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def output_dir(settings: dict, override=None) -> Path:
    return Path(override) if override else ROOT / settings["output_dir"]


def database_path(out_dir: Path) -> Path:
    """The pipeline's SQLite database lives next to the generated files."""
    return Path(out_dir) / "observability.db"
