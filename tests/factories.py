from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from glabviz.domain import (
    AttemptKind,
    JobAttempt,
    PipelineSnapshot,
    SnapshotCompleteness,
    SourcePresence,
)

BASE = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
_MISSING: Any = object()


def attempt(
    job_id: int = 1,
    *,
    pipeline_id: int = 1,
    name: str = "build",
    stage: str = "build",
    status: str = "success",
    start: datetime | None = BASE,
    finish: Any = _MISSING,
    queued: float | None = 0.0,
    allow_failure: bool = False,
    kind: AttemptKind = AttemptKind.JOB,
    observed_failure: bool | None = None,
    is_current: bool | None = True,
    created: datetime | None = None,
    source_presence: SourcePresence = SourcePresence.PRESENT,
) -> JobAttempt:
    if finish is _MISSING:
        finish = start + timedelta(minutes=5) if start is not None else None
    if observed_failure is None:
        observed_failure = status == "failed"
    return JobAttempt(
        job_id=job_id,
        pipeline_id=pipeline_id,
        exact_name=name,
        stage=stage,
        kind=kind,
        source_status=status,
        allow_failure=allow_failure,
        observed_failure=observed_failure,
        is_current_attempt=is_current,
        created_at=created,
        started_at=start,
        finished_at=finish,
        gitlab_duration_s=None,
        queued_duration_s=queued,
        runner_id=None,
        web_url=None,
        failure_reason=None,
        source_presence=source_presence,
    )


def snapshot(
    pipeline_id: int = 1,
    *,
    status: str = "success",
    attempts: tuple[JobAttempt, ...] = (),
    created_at: datetime = BASE - timedelta(minutes=10),
    started_at: datetime | None = BASE - timedelta(minutes=10),
    finished_at: datetime | None = BASE + timedelta(minutes=20),
    gitlab_duration_s: float | None = 600.0,
    sha: str = "sha",
    completeness: SnapshotCompleteness = SnapshotCompleteness.COMPLETE,
    source: str = "merge_request_event",
    ref: str | None = None,
    name: str = "pipeline",
    instance_base_url: str = "https://gitlab.example.com",
    project_id: int = 1,
    snapshot_error: str | None = None,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        instance_base_url=instance_base_url,
        project_id=project_id,
        pipeline_id=pipeline_id,
        iid=pipeline_id,
        name=name,
        ref=ref or f"refs/merge-requests/{pipeline_id}/head",
        source=source,
        sha=sha,
        source_status=status,
        created_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        updated_at=finished_at,
        gitlab_duration_s=gitlab_duration_s,
        web_url=f"{instance_base_url}/pipelines/{pipeline_id}",
        attempts=attempts,
        completeness=completeness,
        snapshot_error=snapshot_error,
    )
