from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from glabviz.collector import Collector, CollectorError, SyncRequest, normalize_pipeline
from glabviz.domain import (
    MetricPeriod,
    PipelineSnapshot,
    ProjectInfo,
    SnapshotCompleteness,
)
from glabviz.gitlab_client import (
    GitLabAuthError,
    GitLabRequestError,
    PipelineList,
    RawSnapshot,
)
from glabviz.runtime import Runtime, SyncAlreadyRunningError
from glabviz.storage import Storage

INSTANCE = "https://gitlab.example.com"
PERIOD = MetricPeriod(date(2026, 5, 1), date(2026, 5, 31), "UTC")
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2027, 1, 1, tzinfo=UTC)


def _job(job_id: int, *, status: str = "success") -> dict:
    return {
        "id": job_id,
        "name": "build",
        "stage": "build",
        "status": status,
        "allow_failure": False,
        "created_at": "2026-05-10T10:00:00Z",
        "started_at": "2026-05-10T10:00:01Z",
        "finished_at": "2026-05-10T10:05:00Z",
        "queued_duration": 1.0,
        "duration": 299.0,
        "web_url": f"{INSTANCE}/jobs/{job_id}",
    }


def _raw(
    pipeline_id: int,
    *,
    created_at: str = "2026-05-10T10:00:00Z",
    status: str = "success",
    jobs: tuple[dict, ...] = (),
    completeness: SnapshotCompleteness = SnapshotCompleteness.COMPLETE,
) -> RawSnapshot:
    detail = {
        "id": pipeline_id,
        "iid": pipeline_id,
        "project_id": 1,
        "status": status,
        "source": "merge_request_event",
        "ref": f"refs/merge-requests/{pipeline_id}/head",
        "name": f"pipeline-{pipeline_id}",
        "sha": "sha",
        "created_at": created_at,
        "started_at": "2026-05-10T10:00:00Z",
        "finished_at": "2026-05-10T10:10:00Z",
        "updated_at": "2026-05-10T10:10:00Z",
        "duration": 600,
        "web_url": f"{INSTANCE}/pipelines/{pipeline_id}",
    }
    return RawSnapshot(
        instance_base_url=INSTANCE,
        project_id=1,
        detail=detail,
        jobs=jobs,
        current_job_ids=frozenset(job["id"] for job in jobs),
        bridges=(),
        current_bridge_ids=frozenset(),
        bridges_supported=True,
        completeness=completeness,
        snapshot_error=None,
    )


class FakeClient:
    def __init__(
        self,
        raws: dict[int, RawSnapshot],
        *,
        warnings: tuple[str, ...] = (),
        lossy: bool = False,
        failing: dict[int, Exception] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.raws = raws
        self.warnings = warnings
        self.lossy = lossy
        self.failing = failing or {}
        self.list_error = list_error
        self.project = ProjectInfo(
            project_id=1,
            path="group/project",
            name="Project",
            default_branch="main",
        )

    def check_project(self, project: str | int) -> ProjectInfo:
        return self.project

    def list_pipelines(self, project, created_after, created_before, **kwargs):
        if self.list_error is not None:
            raise self.list_error
        return PipelineList(
            pipeline_ids=tuple(self.raws),
            warnings=self.warnings,
            lossy=self.lossy,
        )

    def fetch_snapshot(self, project: str | int, pipeline_id: int) -> RawSnapshot:
        error = self.failing.get(pipeline_id)
        if error is not None:
            raise error
        return self.raws[pipeline_id]


@pytest.fixture
def storage(tmp_path):
    store = Storage.open(tmp_path / "data")
    yield store
    store.close()


def _request(workers: int = 2) -> SyncRequest:
    return SyncRequest(
        instance_base_url=INSTANCE,
        project="1",
        period=PERIOD,
        workers=workers,
    )


def _sync(store: Storage, client: FakeClient, progress=None, workers: int = 2):
    collector = Collector(store, lambda: client)
    return collector.sync(_request(workers), progress)


def _stored(store: Storage) -> list[PipelineSnapshot]:
    return store.load_snapshots(INSTANCE, 1, START, END)


def test_sync_publishes_pipelines_and_records_run(storage):
    client = FakeClient(
        {
            1: _raw(1, jobs=(_job(10),)),
            2: _raw(2, jobs=(_job(20, status="failed"),)),
        }
    )
    result = _sync(storage, client)
    assert result.state == "complete"
    assert result.discovered == 2
    assert result.completed == 2
    stored = _stored(storage)
    assert len(stored) == 2
    assert {snapshot.pipeline_id for snapshot in stored} == {1, 2}
    runs = storage.sync_runs(INSTANCE, 1)
    assert runs[0].state == "complete"
    assert runs[0].completed_count == 2


def test_pipeline_error_marks_sync_partial_and_keeps_other_pipelines(storage):
    client = FakeClient(
        {1: _raw(1, jobs=(_job(10),)), 2: _raw(2, jobs=(_job(20),))},
        failing={2: GitLabRequestError("boom")},
    )
    result = _sync(storage, client)
    assert result.state == "partial"
    assert result.completed == 1
    assert result.errors
    stored = _stored(storage)
    assert [snapshot.pipeline_id for snapshot in stored] == [1]
    assert 2 not in {snapshot.pipeline_id for snapshot in stored}


def test_auth_error_aborts_sync_without_publishing(storage):
    client = FakeClient({1: _raw(1), 2: _raw(2)}, list_error=GitLabAuthError("401"))
    result = _sync(storage, client)
    assert result.state == "failed"
    assert result.completed == 0
    assert _stored(storage) == []


def test_auth_error_during_pipeline_fetch_marks_failed(storage):
    client = FakeClient(
        {1: _raw(1, jobs=(_job(10),)), 2: _raw(2, jobs=(_job(20),))},
        failing={2: GitLabAuthError("403")},
    )
    result = _sync(storage, client, workers=1)
    assert result.state == "failed"
    assert result.completed == 1
    assert result.errors


def test_pipeline_outside_period_is_not_published(storage):
    client = FakeClient({1: _raw(1, created_at="2026-04-01T10:00:00Z", jobs=(_job(10),))})
    result = _sync(storage, client)
    assert result.completed == 0
    assert result.warnings
    assert _stored(storage) == []


def test_lossy_listing_marks_sync_partial(storage):
    client = FakeClient({1: _raw(1, jobs=(_job(10),))}, lossy=True)
    result = _sync(storage, client)
    assert result.state == "partial"


def test_progress_callback_reports_completed_counts(storage):
    client = FakeClient({1: _raw(1, jobs=(_job(10),)), 2: _raw(2, jobs=(_job(20),))})
    updates: list[tuple[int, int, str]] = []
    _sync(storage, client, lambda done, total, message: updates.append((done, total, message)))
    assert updates[-1][0] == 2
    assert updates[-1][1] == 2


def test_unexpected_pipeline_payload_is_an_error(storage):
    raw = _raw(1)
    raw = RawSnapshot(
        instance_base_url=raw.instance_base_url,
        project_id=raw.project_id,
        detail={**raw.detail, "created_at": None},
        jobs=raw.jobs,
        current_job_ids=raw.current_job_ids,
        bridges=raw.bridges,
        current_bridge_ids=raw.current_bridge_ids,
        bridges_supported=True,
        completeness=raw.completeness,
    )
    client = FakeClient({1: raw})
    result = _sync(storage, client)
    assert result.state == "failed"
    assert result.errors


def test_second_sync_is_rejected_by_process_wide_lock(storage):
    runtime = Runtime(storage)
    with runtime.sync_guard(), pytest.raises(SyncAlreadyRunningError), runtime.sync_guard():
        pass


def test_normalize_pipeline_has_no_unused_fetched_at_parameter():
    assert list(inspect.signature(normalize_pipeline).parameters) == ["raw"]


def _bridge(job_id: int) -> dict:
    return {
        "id": job_id,
        "name": "run_e2e",
        "stage": "e2e_test",
        "status": "success",
        "allow_failure": False,
        "created_at": "2026-05-10T10:00:00Z",
        "started_at": "2026-05-10T10:00:01Z",
        "finished_at": "2026-05-10T10:05:00Z",
        "queued_duration": 1.0,
        "duration": 299.0,
        "web_url": f"{INSTANCE}/jobs/{job_id}",
    }


def test_job_and_bridge_id_collision_raises_instead_of_dropping_the_bridge(storage):
    raw = replace(
        _raw(1, jobs=(_job(10),)),
        bridges=(_bridge(10),),
        current_bridge_ids=frozenset({10}),
    )
    with pytest.raises(CollectorError):
        normalize_pipeline(raw)

    good = _raw(2, jobs=(_job(20),))
    result = _sync(storage, FakeClient({1: raw, 2: good}))
    assert result.state == "partial"
    assert result.completed == 1
    assert any("job id 10" in error for error in result.errors)
    assert {snapshot.pipeline_id for snapshot in _stored(storage)} == {2}
