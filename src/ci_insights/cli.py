import argparse
import os
import sys
from pathlib import Path

from ci_insights import __version__
from ci_insights.config import DATA_DIR_ENV, data_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci-insights",
        description="Start the CI Insights local app (Streamlit on 127.0.0.1).",
    )
    parser.add_argument("--version", action="version", version=f"ci-insights {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Data directory override (default: ~/.ci-insights or CI_INSIGHTS_DATA_DIR)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8501,
        metavar="PORT",
        help="Port for the Streamlit server (default: 8501)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser on startup"
    )
    return parser


def streamlit_args(args: argparse.Namespace) -> list[str]:
    flags = [
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(args.port),
        "--browser.gatherUsageStats",
        "false",
        "--server.showEmailPrompt",
        "false",
    ]
    if args.no_browser:
        flags += ["--server.headless", "true"]
    return flags


def resolve_data_dir(path: Path | None) -> Path:
    if path is not None:
        os.environ[DATA_DIR_ENV] = str(path)
    resolved = data_dir()
    if resolved.exists() and not resolved.is_dir():
        raise NotADirectoryError(f"data directory exists and is not a directory: {resolved}")
    return resolved


def use_data_dir(path: Path | None) -> Path:
    resolved = resolve_data_dir(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        use_data_dir(args.data_dir)
    except NotADirectoryError as exc:
        parser.error(str(exc))

    app_path = Path(__file__).with_name("app.py")

    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", str(app_path), *streamlit_args(args)]
    stcli.main()
