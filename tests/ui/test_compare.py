from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from glabviz.ui import keys
from tests.ui.helpers import seed_project, seed_saved_analysis

APP_PATH = Path(__file__).resolve().parents[2] / "src" / "glabviz" / "app.py"


def _open(data_directory, page: str) -> AppTest:
    at = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    at.get_by_key(keys.NAV).set_value(page).run(timeout=40)
    return at


def test_compare_page_renders_both_periods_offline(data_directory, monkeypatch):
    monkeypatch.setattr(
        "glabviz.ui.data.GitLabClient",
        lambda *args, **kwargs: pytest.fail("GitLab client must not be created on render"),
    )
    seed_project(data_directory)
    at = _open(data_directory, "Compare")
    assert not at.exception
    headings = {element.value for element in at.subheader}
    assert {
        "Periods",
        "Filters",
        "Analysis",
        "Metrics",
        "Runs with failures",
        "Wall-clock",
        "Wall-clock distribution",
        "Job queue wait",
        "Dynamics",
        "Stages and jobs",
    } <= headings
    rates = [element.value for element in at.metric if element.label == "Failure rate"]
    assert "100.0% (1/1)" in rates
    assert "0.0% (0/2)" in rates
    assert len(at.get("plotly_chart")) == 5


def test_compare_page_switches_level_and_scope(data_directory):
    seed_project(data_directory)
    at = _open(data_directory, "Compare")
    at.get_by_key(keys.C_LEVEL).set_value("job").run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.C_JOB) is not None
    assert keys.C_STAGE not in {widget.key for widget in at.selectbox}
    at.get_by_key(keys.C_SCOPE_ENABLED).set_value(True).run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.C_SCOPE_STAGES) is not None


def test_compare_keeps_an_explicit_any_source_filter(data_directory):
    seed_project(data_directory, local_filter={"source": None})
    at = _open(data_directory, "Compare")
    assert not at.exception
    assert at.get_by_key(keys.C_SOURCE).value == ""


def test_compare_page_shows_stage_and_job_tables(data_directory):
    seed_project(data_directory)
    at = _open(data_directory, "Compare")
    tables = [frame.value for frame in at.dataframe]
    assert any("object" in frame.columns for frame in tables)
    assert any(set(frame.columns) >= {"metric", "A", "B"} for frame in tables)
    object_table = next(frame for frame in tables if "object" in frame.columns)
    assert "checks" in " ".join(str(value) for value in object_table["object"])


def test_compare_saves_a_pipeline_level_comparison(data_directory):
    seed_project(data_directory)
    at = _open(data_directory, "Compare")
    at.get_by_key(keys.SAVE_NAME).set_value("Pipeline save").run(timeout=40)
    at.get_by_key("save_new_compare").click().run(timeout=40)
    assert not at.exception
    assert any("Saved 'Pipeline save'" in element.value for element in at.success)


def test_dynamics_text_follows_the_selected_metric(data_directory):
    seed_project(data_directory)
    at = _open(data_directory, "Compare")
    at.session_state[keys.C_DYNAMICS_METRIC] = "pending_mean"
    at.run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.chart_key("dynamics", "y")).value == "Pending mean"


def test_timezone_survives_the_first_navigation_between_pages(data_directory):
    seed_project(data_directory)
    at = _open(data_directory, "Compare")
    at.get_by_key(keys.C_TIMEZONE).set_value("Europe/Moscow").run(timeout=40)
    at.get_by_key(keys.NAV).set_value("Gantt").run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.C_TIMEZONE).value == "Europe/Moscow"
    at.get_by_key(keys.NAV).set_value("Compare").run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.C_TIMEZONE).value == "Europe/Moscow"


def test_selecting_an_existing_comparison_is_not_overridden_by_the_last_save(data_directory):
    seed_project(data_directory)
    alpha = seed_saved_analysis(data_directory, name="Alpha")
    seed_saved_analysis(data_directory, name="Beta")
    at = _open(data_directory, "Compare")
    at.get_by_key(keys.SAVE_NAME).set_value("Gamma").run(timeout=40)
    at.get_by_key("save_new_compare").click().run(timeout=40)
    assert not at.exception

    at.get_by_key(keys.SELECTED_ANALYSIS).set_value(alpha).run(timeout=40)
    assert at.get_by_key(keys.SELECTED_ANALYSIS).value == alpha
    at.get_by_key(keys.SAVE_NAME).set_value("Alpha updated").run(timeout=40)
    at.get_by_key("save_update_compare").click().run(timeout=40)
    assert not at.exception
    assert any("Updated 'Alpha updated'" in element.value for element in at.success)
