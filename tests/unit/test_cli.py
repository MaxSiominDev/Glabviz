import os

import pytest

from ci_insights import __version__
from ci_insights.cli import build_parser, main, streamlit_args, use_data_dir
from ci_insights.config import DATA_DIR_ENV


def test_help_lists_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--version", "--data-dir", "--port", "--no-browser"):
        assert flag in out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_defaults():
    args = build_parser().parse_args([])
    assert args.port == 8501
    assert args.data_dir is None
    assert args.no_browser is False


def test_unknown_flag_rejected():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--nope"])
    assert exc.value.code == 2


def test_streamlit_server_flags():
    args = build_parser().parse_args([])
    assert streamlit_args(args) == [
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8501",
        "--browser.gatherUsageStats",
        "false",
        "--server.showEmailPrompt",
        "false",
    ]


def test_streamlit_no_browser_sets_headless():
    args = build_parser().parse_args(["--no-browser", "--port", "9000"])
    flags = streamlit_args(args)
    assert flags[-2:] == ["--server.headless", "true"]
    assert flags[flags.index("--server.port") + 1] == "9000"


def test_use_data_dir_override(monkeypatch, tmp_path):
    target = tmp_path / "data"
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert use_data_dir(target) == target
    assert os.environ[DATA_DIR_ENV] == str(target)
    assert target.is_dir()


def test_use_data_dir_default(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "default"))
    assert use_data_dir(None) == tmp_path / "default"
    assert (tmp_path / "default").is_dir()


def test_data_dir_rejects_existing_file(monkeypatch, tmp_path, capsys):
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory")
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--data-dir", str(occupied)])
    assert exc.value.code == 2
    assert "not a directory" in capsys.readouterr().err
