from __future__ import annotations

from glabviz.ui.analysis_state import job_selection


def test_job_selection_uses_the_stage_encoded_in_the_selected_job():
    assert job_selection("", "checks\x00lint") == ("checks", "lint")
    assert job_selection("deploy", "checks\x00lint") == ("checks", "lint")
    assert job_selection("deploy", "") == ("deploy", None)
    assert job_selection("", "") == (None, None)
