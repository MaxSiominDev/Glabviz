from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ci_insights.domain import ProjectInfo, SnapshotCompleteness
from ci_insights.gitlab_client import PipelineList, RawSnapshot
from ci_insights.storage import Storage
from tests.factories import attempt, snapshot

APP_PATH = Path(__file__).resolve().parents[2] / "src" / "ci_insights" / "app.py"


@pytest.fixture(autouse=True)
def clear_runtime_cache():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def test_app_renders_without_data(data_directory):
    at = AppTest.from_file(str(APP_PATH)).run(timeout=15)
    assert not at.exception
    assert at.title[0].value == "CI Insights"
    assert any("No data is stored yet" in info.value for info in at.info)


def test_app_shows_stored_pipelines_offline(data_directory, monkeypatch):
    monkeypatch.setattr(
        "ci_insights.ui.data.GitLabClient",
        lambda *args, **kwargs: pytest.fail("GitLab client must not be created on render"),
    )
    store = Storage.open(data_directory)
    try:
        store.upsert_project(
            ProjectInfo(
                project_id=1,
                path="group/project",
                name="Project",
                default_branch="main",
            ),
            "https://gitlab.example.com",
            "UTC",
            {},
        )
        store.publish_snapshot(
            snapshot(
                42,
                attempts=(attempt(job_id=420, pipeline_id=42, name="build"),),
            )
        )
    finally:
        store.close()

    at = AppTest.from_file(str(APP_PATH)).run(timeout=15)
    assert not at.exception
    frames = [frame.value for frame in at.dataframe if "pipeline" in frame.value.columns]
    assert frames, "recent pipelines table is missing"
    assert 42 in frames[0]["pipeline"].tolist()
    assert "green" in frames[0]["classification"].tolist()


class FakeGitLabClient:
    def __init__(self, settings):
        self.settings = settings

    def check_project(self, project):
        return ProjectInfo(
            project_id=1,
            path="group/project",
            name="Project",
            default_branch="main",
        )

    def list_pipelines(self, project, created_after, created_before, **kwargs):
        return PipelineList(pipeline_ids=(7,))

    def fetch_snapshot(self, project, pipeline_id):
        detail = {
            "id": pipeline_id,
            "iid": pipeline_id,
            "project_id": 1,
            "status": "success",
            "source": "merge_request_event",
            "ref": f"refs/merge-requests/{pipeline_id}/head",
            "name": "pipeline",
            "sha": "sha",
            "created_at": "2026-05-10T10:00:00Z",
            "started_at": "2026-05-10T10:00:00Z",
            "finished_at": "2026-05-10T10:10:00Z",
            "updated_at": "2026-05-10T10:10:00Z",
            "duration": 600,
            "web_url": "https://gitlab.example.com/pipelines/7",
        }
        job = {
            "id": 70,
            "name": "build",
            "stage": "build",
            "status": "success",
            "allow_failure": False,
            "created_at": "2026-05-10T10:00:00Z",
            "started_at": "2026-05-10T10:00:01Z",
            "finished_at": "2026-05-10T10:05:00Z",
            "queued_duration": 1.0,
            "duration": 299.0,
            "web_url": "https://gitlab.example.com/jobs/70",
        }
        return RawSnapshot(
            instance_base_url="https://gitlab.example.com",
            project_id=1,
            detail=detail,
            jobs=(job,),
            current_job_ids=frozenset({70}),
            bridges=(),
            current_bridge_ids=frozenset(),
            bridges_supported=True,
            completeness=SnapshotCompleteness.COMPLETE,
        )


def test_load_button_persists_pipeline(data_directory, monkeypatch):
    monkeypatch.setattr("ci_insights.ui.data.GitLabClient", FakeGitLabClient)
    at = AppTest.from_file(str(APP_PATH)).run(timeout=15)
    at.text_input[0].set_value("https://gitlab.example.com")
    at.text_input[1].set_value("1")
    at.text_input[2].set_value("token")
    at.date_input[0].set_value(date(2026, 5, 1))
    at.date_input[1].set_value(date(2026, 5, 31))
    at.button[1].click().run(timeout=15)
    assert not at.exception
    frames = [frame.value for frame in at.dataframe if "pipeline" in frame.value.columns]
    assert frames, "recent pipelines table is missing after sync"
    assert 7 in frames[0]["pipeline"].tolist()
