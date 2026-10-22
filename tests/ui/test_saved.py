from __future__ import annotations

from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from glabviz.ui import keys
from tests.ui.helpers import INSTANCE, seed_project, seed_saved_analysis

APP_PATH = Path(__file__).resolve().parents[2] / "src" / "glabviz" / "app.py"


def _open(data_directory, page: str = "Saved") -> AppTest:
    at = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    at.get_by_key(keys.NAV).set_value(page).run(timeout=40)
    return at


def test_saved_page_opens_a_comparison(data_directory):
    seed_project(data_directory)
    analysis_id = seed_saved_analysis(data_directory)
    at = _open(data_directory)
    assert not at.exception
    assert at.get_by_key(keys.SELECTED_ANALYSIS).value == analysis_id
    assert any("Smoke saved" in str(element.value) for element in at.markdown)

    at.get_by_key("saved_open").click().run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.NAV).value == "Compare"
    assert at.get_by_key(keys.C_A_START).value == date(2026, 5, 1)
    assert at.get_by_key(keys.C_A_END).value == date(2026, 5, 31)


def test_saved_page_renames_and_deletes(data_directory):
    seed_project(data_directory)
    seed_saved_analysis(data_directory)
    at = _open(data_directory)
    at.get_by_key(keys.SAVED_RENAME_NAME).set_value("Renamed").run(timeout=40)
    at.get_by_key("saved_rename").click().run(timeout=40)
    assert not at.exception
    assert any("Renamed" in element.value for element in at.success)

    at.get_by_key(keys.SAVED_DELETE_CONFIRM).set_value(True).run(timeout=40)
    at.get_by_key("saved_delete").click().run(timeout=40)
    assert not at.exception
    assert any("No saved comparisons yet" in element.value for element in at.info)


def test_manual_changes_after_open_are_not_overwritten_by_the_other_page(data_directory):
    seed_project(data_directory)
    seed_saved_analysis(data_directory)
    at = _open(data_directory)
    at.get_by_key("saved_open").click().run(timeout=40)
    assert not at.exception
    at.get_by_key(keys.C_TIMEZONE).set_value("Europe/Moscow").run(timeout=40)
    at.get_by_key(keys.NAV).set_value("Gantt").run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.C_TIMEZONE).value == "Europe/Moscow"


def test_hand_written_config_gets_metric_defaults_for_the_dynamics_chart(data_directory):
    seed_project(data_directory)
    seed_saved_analysis(data_directory, dynamics_metric="pending_mean")
    at = _open(data_directory)
    at.get_by_key("saved_open").click().run(timeout=40)
    assert not at.exception
    assert at.get_by_key(keys.chart_key("dynamics", "y")).value == "Pending mean"


def test_switching_project_clears_the_active_comparison(data_directory):
    seed_project(data_directory)
    seed_project(data_directory, project_id=2, path="group/other", with_pipelines=False)
    seed_saved_analysis(data_directory, name="First project")
    at = _open(data_directory)
    at.get_by_key("saved_open").click().run(timeout=40)
    assert not at.exception
    assert keys.ACTIVE_ANALYSIS_ID in at.session_state

    at.get_by_key(keys.C_PROJECT).set_value(f"{INSTANCE}|2").run(timeout=40)
    assert not at.exception
    assert any(
        "No saved comparisons yet for this project" in element.value for element in at.caption
    )
    assert at.get_by_key("save_update_compare").disabled
