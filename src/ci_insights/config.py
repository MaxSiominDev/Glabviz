import logging
import os
from pathlib import Path

DATA_DIR_ENV = "CI_INSIGHTS_DATA_DIR"
GITLAB_URL_ENV = "CI_INSIGHTS_GITLAB_URL"
GITLAB_TOKEN_ENV = "CI_INSIGHTS_GITLAB_TOKEN"
CA_BUNDLE_ENV = "CI_INSIGHTS_CA_BUNDLE"
REQUESTS_CA_BUNDLE_ENV = "REQUESTS_CA_BUNDLE"

DEFAULT_DATA_DIR = Path.home() / ".ci-insights"


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override) if override else DEFAULT_DATA_DIR


def gitlab_url() -> str | None:
    return os.environ.get(GITLAB_URL_ENV) or None


def gitlab_token() -> str | None:
    return os.environ.get(GITLAB_TOKEN_ENV) or None


def ca_bundle() -> str | None:
    return os.environ.get(CA_BUNDLE_ENV) or os.environ.get(REQUESTS_CA_BUNDLE_ENV) or None


def configure_logging(directory: Path) -> None:
    log_dir = directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ci_insights")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
