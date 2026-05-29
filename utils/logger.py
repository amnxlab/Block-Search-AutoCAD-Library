"""
Application logger — rotating file + stderr handler.
"""
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any, Dict

_LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(config: Dict[str, Any]) -> None:
    level_name: str = config.get("log_level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on reload
    if root.handlers:
        return

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Stderr handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler
    log_file = config.get("log_file", "data/app.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass  # Read-only environment — skip file logging


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
