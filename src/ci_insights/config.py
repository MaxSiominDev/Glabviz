import os
from pathlib import Path

DATA_DIR_ENV = "CI_INSIGHTS_DATA_DIR"
DEFAULT_DATA_DIR = Path.home() / ".ci-insights"


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override) if override else DEFAULT_DATA_DIR
