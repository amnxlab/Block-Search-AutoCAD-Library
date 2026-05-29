"""
Configuration loader — reads config.json from project root or user data dir.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

_DEFAULT_CONFIG: Dict[str, Any] = {
    "oda_converter_path": "vendor/ODAFileConverter/ODAFileConverter.exe",
    "db_path": "data/index.db",
    "scan_paths": [],
    "scan_extensions": [".dwg", ".dwt"],
    "skip_anonymous_blocks": True,
    "fuzzy_threshold": 60,
    "max_results": 200,
    "debounce_ms": 300,
    "theme": "dark",
    "log_level": "INFO",
    "log_file": "data/app.log",
    "temp_dir": "",
    "preview_prefer_acad": True,
}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def _resolve_path(config: Dict[str, Any], key: str, base: Path) -> None:
    """Convert relative paths stored in config to absolute paths."""
    raw = config.get(key, "")
    if raw and not os.path.isabs(raw):
        config[key] = str(base / raw)


def load_config() -> Dict[str, Any]:
    base = _get_base_dir()
    config_path = base / "config.json"

    config = dict(_DEFAULT_CONFIG)

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            config.update(loaded)
        except (json.JSONDecodeError, OSError):
            pass  # fall back to defaults

    # Resolve relative paths to absolute
    for key in ("oda_converter_path", "db_path", "log_file"):
        _resolve_path(config, key, base)

    # Ensure data directory exists
    db_dir = Path(config["db_path"]).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    # Ensure log directory exists
    log_dir = Path(config["log_file"]).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    config["_base_dir"] = str(base)

    return config


def save_config(config: Dict[str, Any]) -> None:
    base = Path(config.get("_base_dir", _get_base_dir()))
    config_path = base / "config.json"

    # Strip internal keys before saving
    to_save = {k: v for k, v in config.items() if not k.startswith("_")}

    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(to_save, fh, indent=2)
