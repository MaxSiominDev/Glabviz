from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from glabviz.ui import keys
from tests.ui.helpers import seed_project, seed_saved_analysis

APP_PATH = Path(__file__).resolve().parents[2] / "src" / "glabviz" / "app.py"


def _open(data_directory) -> AppTest:
    at = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    at.get_by_key(keys.NAV).set_value("Gantt").run(timeout=40)
    return at


def test_gantt_page_renders_and_compares_two_pipelines(data_directory):
    seed_project(data_directory)
    at = _open(data_directory)
    assert not at.exception
    headings = {element.value for element in at.subheader}
    assert {"Rows", "Layout", "Attempts"} <= headings
    assert len(at.get("plotly_chart")) == 1

    at.get_by_key(keys.G_PIPELINE_B).set_value(1).run(timeout=40)
    at.get_by_key("show_all_stages").click().run(timeout=40)
    at.get_by_key("show_all_jobs").click().run(timeout=40)
    assert not at.exception
    tables = [frame.value for frame in at.dataframe]
    attempt_tables = [frame for frame in tables if "pipeline" in frame.columns]
    assert attempt_tables
    assert {1, 3} <= set(attempt_tables[0]["pipeline"])


def test_gantt_page_hides_stages_and_lists_unplotted_attempts(data_directory):
    seed_project(data_directory)
    at = _open(data_directory)
    at.get_by_key(keys.G_VISIBLE_STAGES).set_value(["checks", "deploy"]).run(timeout=40)
    assert not at.exception

    at.get_by_key(keys.G_PIPELINE_B).set_value(None).run(timeout=40)
    at.get_by_key(keys.G_PIPELINE_A).set_value(4).run(timeout=40)
    assert not at.exception
    tables = [frame.value for frame in at.dataframe]
    reasons = [frame for frame in tables if "reason" in frame.columns]
    assert reasons
    assert any(str(value).strip() for value in reasons[0]["reason"])


def test_missing_saved_pipeline_is_reported_and_replaced(data_directory):
    seed_project(data_directory)
    seed_saved_analysis(data_directory, primary=999, secondary=None)
    at = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    at.get_by_key(keys.NAV).set_value("Saved").run(timeout=40)
    at.get_by_key("saved_open").click().run(timeout=40)
    at.get_by_key(keys.NAV).set_value("Gantt").run(timeout=40)
    assert not at.exception
    assert any("not stored" in element.value for element in at.warning)
