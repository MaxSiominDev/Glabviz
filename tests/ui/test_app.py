from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[2] / "src" / "ci_insights" / "app.py"


def test_app_renders_without_exception():
    at = AppTest.from_file(str(APP_PATH)).run(timeout=15)
    assert not at.exception
    assert at.title[0].value == "CI Insights"
