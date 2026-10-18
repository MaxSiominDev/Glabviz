from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from requests import PreparedRequest

from ci_insights.domain import SnapshotCompleteness
from ci_insights.gitlab_client import (
    GitLabAuthError,
    GitLabClient,
    GitLabRequestError,
    GitLabSettings,
    PipelineList,
    canonical_instance_url,
)

BASE_URL = "https://gitlab.example.com"
AFTER = datetime(2026, 5, 1, tzinfo=UTC)
BEFORE = datetime(2026, 6, 1, tzinfo=UTC)


def _settings(*, per_page: int = 100, max_retries: int = 2, snapshot_retries: int = 1):
    return GitLabSettings(
        base_url=BASE_URL,
        token="token",
        per_page=per_page,
        max_retries=max_retries,
        snapshot_retries=snapshot_retries,
    )


def _pipeline(pid: int, created_at: str, source: str = "merge_request_event") -> dict:
    return {
        "id": pid,
        "iid": pid,
        "project_id": 1,
        "status": "success",
        "source": source,
        "ref": f"refs/merge-requests/{pid}/head",
        "name": f"pipeline-{pid}",
        "sha": "sha",
        "created_at": created_at,
        "updated_at": created_at,
        "web_url": f"{BASE_URL}/pipelines/{pid}",
    }


def _detail(pid: int, *, updated_at: str = "2026-05-04T10:00:00Z") -> dict:
    return {
        **_pipeline(pid, "2026-05-04T09:50:00Z"),
        "started_at": "2026-05-04T10:00:00Z",
        "finished_at": "2026-05-04T10:40:00Z",
        "duration": 2400,
        "iid": pid,
        "updated_at": updated_at,
    }


def _job(job_id: int, *, status: str = "success", name: str = "build") -> dict:
    return {
        "id": job_id,
        "name": name,
        "stage": "build",
        "status": status,
        "allow_failure": False,
        "created_at": "2026-05-04T10:00:00Z",
        "started_at": "2026-05-04T10:00:01Z",
        "finished_at": "2026-05-04T10:05:00Z",
        "queued_duration": 1.0,
        "duration": 299.0,
        "web_url": f"{BASE_URL}/jobs/{job_id}",
    }


class FakeGitLab:
    def __init__(self) -> None:
        self.requests: list[PreparedRequest] = []
        self.pipelines: list[dict] = []
        self.details: dict[int, list[dict]] = {}
        self.jobs: dict[int, list[dict]] = {}
        self.current_jobs: dict[int, list[dict]] = {}
        self.bridges: dict[int, list[dict]] = {}
        self.current_bridges: dict[int, list[dict]] = {}
        self.bridge_status = 200
        self.pipeline_filter_status = 0
        self.reject_include_retried = False
        self.jobs_error_page = 0
        self.jobs_error_status = 0
        self._detail_calls: dict[int, int] = defaultdict(int)

    def install(self) -> None:
        responses.add_callback(
            responses.GET,
            re.compile(r"https://gitlab\.example\.com/api/v4/.*"),
            callback=self._handle,
        )

    def detail(self, pid: int, versions: list[dict]) -> None:
        self.details[pid] = versions

    def _handle(self, request: PreparedRequest):
        self.requests.append(request)
        parsed = urlparse(request.url)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/v4/projects/1":
            return (
                200,
                {},
                json.dumps(
                    {
                        "id": 1,
                        "path_with_namespace": "group/project",
                        "name": "Project",
                        "default_branch": "main",
                    }
                ),
            )
        if path == "/api/v4/projects/1/pipelines":
            if self.pipeline_filter_status and "created_after" in query:
                return (
                    self.pipeline_filter_status,
                    {},
                    json.dumps({"message": "created_after is not supported"}),
                )
            return self._page(self.pipelines, query)
        match = re.fullmatch(r"/api/v4/projects/1/pipelines/(\d+)", path)
        if match:
            pid = int(match.group(1))
            versions = self.details.get(pid, [_detail(pid)])
            index = min(self._detail_calls[pid], len(versions) - 1)
            self._detail_calls[pid] += 1
            return (200, {}, json.dumps(versions[index]))
        match = re.fullmatch(r"/api/v4/projects/1/pipelines/(\d+)/jobs", path)
        if match:
            pid = int(match.group(1))
            page = int(query.get("page", ["1"])[0])
            if self.jobs_error_page == page and self.jobs_error_status:
                return (
                    self.jobs_error_status,
                    {},
                    json.dumps({"message": "jobs error"}),
                )
            if self.reject_include_retried and query.get("include_retried") == ["true"]:
                return (
                    400,
                    {},
                    json.dumps({"message": "include_retried is not supported"}),
                )
            retried = query.get("include_retried", ["false"])[0]
            items = self.jobs.get(pid, []) if retried == "true" else self.current_jobs.get(pid, [])
            return self._page(items, query)
        match = re.fullmatch(r"/api/v4/projects/1/pipelines/(\d+)/bridges", path)
        if match:
            if self.bridge_status != 200:
                return (self.bridge_status, {}, json.dumps({"message": "bridges"}))
            pid = int(match.group(1))
            retried = query.get("include_retried", ["false"])[0]
            items = (
                self.bridges.get(pid, [])
                if retried == "true"
                else self.current_bridges.get(pid, [])
            )
            return self._page(items, query)
        return (404, {}, json.dumps({"message": "not found"}))

    @staticmethod
    def _page(items: list[dict], query: dict[str, list[str]]):
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["100"])[0])
        start = (page - 1) * per_page
        end = start + per_page
        batch = items[start:end]
        total_pages = max(1, (len(items) + per_page - 1) // per_page)
        headers = {
            "X-Next-Page": str(page + 1) if end < len(items) else "",
            "X-Total-Pages": str(total_pages),
        }
        return (200, headers, json.dumps(batch))


@responses.activate
def test_check_project():
    fake = FakeGitLab()
    fake.install()
    info = GitLabClient(_settings()).check_project("1")
    assert info.project_id == 1
    assert info.path == "group/project"


@responses.activate
def test_pipeline_list_paginates_and_filters_locally():
    fake = FakeGitLab()
    fake.pipelines = [
        _pipeline(1, "2026-05-02T10:00:00Z"),
        _pipeline(2, "2026-04-02T10:00:00Z"),
        _pipeline(3, "2026-05-03T10:00:00Z", source="push"),
    ]
    fake.install()
    result = GitLabClient(_settings(per_page=1)).list_pipelines(
        "1", AFTER, BEFORE, source="merge_request_event"
    )
    assert result.pipeline_ids == (1,)
    assert not result.lossy


@responses.activate
def test_failed_attempt_on_second_page_is_kept_and_include_retried_is_lowercase():
    fake = FakeGitLab()
    failed = _job(2, status="failed")
    success = _job(3)
    fake.detail(10, [_detail(10)])
    fake.jobs[10] = [success, failed]
    fake.current_jobs[10] = [success]
    fake.install()
    snapshot = GitLabClient(_settings(per_page=1, snapshot_retries=0)).fetch_snapshot("1", 10)
    assert {job["id"] for job in snapshot.jobs} == {3, 2}
    urls = [request.url for request in fake.requests]
    assert any("include_retried=true" in url for url in urls)
    assert any("include_retried=false" in url for url in urls)


@responses.activate
def test_bridges_404_degrades_to_unsupported():
    fake = FakeGitLab()
    fake.detail(10, [_detail(10)])
    fake.jobs[10] = [_job(1)]
    fake.current_jobs[10] = [_job(1)]
    fake.bridge_status = 404
    fake.install()
    snapshot = GitLabClient(_settings(snapshot_retries=0)).fetch_snapshot("1", 10)
    assert snapshot.bridges_supported is False
    assert snapshot.completeness is SnapshotCompleteness.BRIDGES_UNSUPPORTED


@responses.activate
def test_bridges_403_is_an_auth_error():
    fake = FakeGitLab()
    fake.detail(10, [_detail(10)])
    fake.jobs[10] = [_job(1)]
    fake.current_jobs[10] = [_job(1)]
    fake.bridge_status = 403
    fake.install()
    with pytest.raises(GitLabAuthError):
        GitLabClient(_settings(snapshot_retries=0)).fetch_snapshot("1", 10)


@responses.activate
def test_missing_current_id_marks_inconsistent():
    fake = FakeGitLab()
    fake.detail(10, [_detail(10)])
    fake.jobs[10] = [_job(1)]
    fake.current_jobs[10] = [_job(2)]
    fake.install()
    snapshot = GitLabClient(_settings(snapshot_retries=0)).fetch_snapshot("1", 10)
    assert snapshot.completeness is SnapshotCompleteness.INCONSISTENT
    assert snapshot.snapshot_error is not None


@responses.activate
def test_changed_detail_marks_inconsistent():
    fake = FakeGitLab()
    fake.detail(
        10,
        [
            _detail(10, updated_at="2026-05-04T10:00:00Z"),
            _detail(10, updated_at="2026-05-04T10:01:00Z"),
        ],
    )
    fake.jobs[10] = [_job(1)]
    fake.current_jobs[10] = [_job(1)]
    fake.install()
    snapshot = GitLabClient(_settings(snapshot_retries=0)).fetch_snapshot("1", 10)
    assert snapshot.completeness is SnapshotCompleteness.INCONSISTENT


@responses.activate
def test_rejected_filter_falls_back_to_broad_listing():
    fake = FakeGitLab()
    fake.pipelines = [_pipeline(1, "2026-05-02T10:00:00Z")]
    fake.pipeline_filter_status = 400
    fake.install()
    result = GitLabClient(_settings()).list_pipelines("1", AFTER, BEFORE)
    assert result.pipeline_ids == (1,)
    assert result.warnings


@responses.activate
def test_missing_created_at_is_skipped_and_lossy():
    fake = FakeGitLab()
    fake.pipelines = [{"id": 1, "source": "merge_request_event"}]
    fake.install()
    result = GitLabClient(_settings()).list_pipelines("1", AFTER, BEFORE)
    assert result.pipeline_ids == ()
    assert result.lossy is True


@responses.activate
def test_auth_error_is_not_retried():
    responses.add(
        responses.GET,
        re.compile(r"https://gitlab\.example\.com/api/v4/projects/1/pipelines/10"),
        status=401,
        json={"message": "401 Unauthorized"},
    )
    with pytest.raises(GitLabAuthError):
        GitLabClient(_settings(max_retries=5)).fetch_snapshot("1", 10)
    assert len(responses.calls) == 1


@responses.activate
def test_transient_error_is_retried():
    calls = {"count": 0}

    def callback(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return (500, {}, json.dumps({"message": "server error"}))
        return (
            200,
            {},
            json.dumps(
                {
                    "id": 1,
                    "path_with_namespace": "group/project",
                    "name": "Project",
                    "default_branch": "main",
                }
            ),
        )

    responses.add_callback(
        responses.GET,
        re.compile(r"https://gitlab\.example\.com/api/v4/projects/1$"),
        callback=callback,
    )
    info = GitLabClient(_settings(max_retries=1)).check_project("1")
    assert info.project_id == 1
    assert calls["count"] == 2


@responses.activate
def test_rate_limit_budget_is_finite():
    responses.add_callback(
        responses.GET,
        re.compile(r"https://gitlab\.example\.com/api/v4/projects/1$"),
        callback=lambda request: (
            429,
            {"Retry-After": "0"},
            json.dumps({"message": "slow down"}),
        ),
    )
    client = GitLabClient(_settings(max_retries=2))
    with pytest.raises(GitLabRequestError) as exc_info:
        client.check_project("1")
    assert exc_info.value.response_code == 429
    assert len(responses.calls) == 3


@responses.activate
def test_include_retried_400_degrades_to_inconsistent():
    fake = FakeGitLab()
    fake.detail(10, [_detail(10)])
    fake.jobs[10] = [_job(1)]
    fake.current_jobs[10] = [_job(1)]
    fake.reject_include_retried = True
    fake.install()
    snapshot = GitLabClient(_settings(snapshot_retries=0)).fetch_snapshot("1", 10)
    assert snapshot.completeness is SnapshotCompleteness.INCONSISTENT
    assert snapshot.current_job_ids == frozenset({1})
    assert snapshot.snapshot_error is not None


@responses.activate
def test_bridges_400_degrades_to_unsupported():
    fake = FakeGitLab()
    fake.detail(10, [_detail(10)])
    fake.jobs[10] = [_job(1)]
    fake.current_jobs[10] = [_job(1)]
    fake.bridge_status = 400
    fake.install()
    snapshot = GitLabClient(_settings(snapshot_retries=0)).fetch_snapshot("1", 10)
    assert snapshot.bridges_supported is False
    assert snapshot.completeness is SnapshotCompleteness.BRIDGES_UNSUPPORTED


@responses.activate
def test_failed_second_jobs_page_raises_without_partial_result():
    fake = FakeGitLab()
    fake.detail(10, [_detail(10)])
    fake.jobs[10] = [_job(1), _job(2)]
    fake.current_jobs[10] = [_job(1), _job(2)]
    fake.jobs_error_page = 2
    fake.jobs_error_status = 500
    fake.install()
    with pytest.raises(GitLabRequestError):
        GitLabClient(_settings(per_page=1, max_retries=0)).fetch_snapshot("1", 10)


@responses.activate
def test_inconsistent_snapshot_is_retried():
    fake = FakeGitLab()
    fake.detail(
        10,
        [
            _detail(10, updated_at="t0"),
            _detail(10, updated_at="t1"),
            _detail(10, updated_at="t1"),
            _detail(10, updated_at="t1"),
        ],
    )
    fake.jobs[10] = [_job(1)]
    fake.current_jobs[10] = [_job(1)]
    fake.install()
    snapshot = GitLabClient(_settings(snapshot_retries=1)).fetch_snapshot("1", 10)
    assert snapshot.completeness is SnapshotCompleteness.COMPLETE
    detail_requests = [
        request
        for request in fake.requests
        if re.fullmatch(r"/api/v4/projects/1/pipelines/10", urlparse(request.url).path)
    ]
    assert len(detail_requests) == 4


def test_canonical_instance_url():
    assert canonical_instance_url("https://host/") == "https://host"
    assert canonical_instance_url("https://host/api/v4/") == "https://host"
    with pytest.raises(ValueError):
        canonical_instance_url("host")


def test_pipeline_list_dataclass_defaults():
    result = PipelineList(pipeline_ids=(1,))
    assert result.warnings == ()
    assert result.lossy is False
