from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from glabviz.domain import (
    AttemptKind,
    JobAttempt,
    MetricPeriod,
    PipelineSnapshot,
    ProjectInfo,
    SourcePresence,
    parse_gitlab_timestamp,
)
from glabviz.gitlab_client import (
    GitLabAuthError,
    GitLabRequestError,
    PipelineList,
    RawSnapshot,
    canonical_instance_url,
)
from glabviz.storage import Storage

logger = logging.getLogger("glabviz.collector")

ProgressCallback = Callable[[int, int, str], None]


class CollectorError(Exception):
    pass


class SupportsGitLab(Protocol):
    def check_project(self, project: str | int) -> ProjectInfo: ...

    def list_pipelines(
        self,
        project: str | int,
        created_after: datetime,
        created_before: datetime,
        *,
        source: str | None = None,
        ref: str | None = None,
        name: str | None = None,
    ) -> PipelineList: ...

    def fetch_snapshot(self, project: str | int, pipeline_id: int) -> RawSnapshot: ...


@dataclass(frozen=True)
class SyncRequest:
    instance_base_url: str
    project: str | int
    period: MetricPeriod
    source: str | None = None
    ref: str | None = None
    name: str | None = None
    workers: int = 4


@dataclass(frozen=True)
class SyncResult:
    sync_id: str
    instance_base_url: str
    project: ProjectInfo
    state: str
    discovered: int
    completed: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _runner_id(payload: dict[str, Any]) -> int | None:
    runner = payload.get("runner")
    if not isinstance(runner, dict):
        return None
    runner_id = runner.get("id")
    try:
        return int(runner_id) if runner_id is not None else None
    except TypeError, ValueError:
        return None


def _attempt_from_payload(
    payload: dict[str, Any],
    pipeline_id: int,
    kind: AttemptKind,
    current_ids: frozenset[int] | None,
) -> JobAttempt:
    job_id = int(payload["id"])
    status = str(payload.get("status") or "")
    return JobAttempt(
        job_id=job_id,
        pipeline_id=pipeline_id,
        exact_name=str(payload.get("name") or ""),
        stage=str(payload.get("stage") or ""),
        kind=kind,
        source_status=status,
        allow_failure=bool(payload.get("allow_failure", False)),
        observed_failure=status == "failed",
        is_current_attempt=(job_id in current_ids) if current_ids is not None else None,
        created_at=parse_gitlab_timestamp(payload.get("created_at")),
        started_at=parse_gitlab_timestamp(payload.get("started_at")),
        finished_at=parse_gitlab_timestamp(payload.get("finished_at")),
        gitlab_duration_s=_optional_float(payload.get("duration")),
        queued_duration_s=_optional_float(payload.get("queued_duration")),
        runner_id=_runner_id(payload),
        web_url=payload.get("web_url"),
        failure_reason=payload.get("failure_reason"),
        source_presence=SourcePresence.PRESENT,
    )


def _index_attempt(index: dict[int, JobAttempt], attempt: JobAttempt) -> None:
    existing = index.get(attempt.job_id)
    if existing is not None:
        if existing.kind is attempt.kind:
            detail = f"twice as a {attempt.kind.value}"
        else:
            detail = f"as both a {existing.kind.value} and a {attempt.kind.value}"
        raise CollectorError(
            f"job id {attempt.job_id} appears {detail} in pipeline {attempt.pipeline_id}"
        )
    index[attempt.job_id] = attempt


def normalize_pipeline(raw: RawSnapshot) -> PipelineSnapshot:
    detail = raw.detail
    created_at = parse_gitlab_timestamp(detail.get("created_at"))
    if created_at is None:
        raise CollectorError(f"pipeline {detail.get('id')} has no valid created_at")
    try:
        pipeline_id = int(detail["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectorError("pipeline payload is missing id") from exc

    attempts_by_id: dict[int, JobAttempt] = {}
    for payload in raw.jobs:
        _index_attempt(
            attempts_by_id,
            _attempt_from_payload(payload, pipeline_id, AttemptKind.JOB, raw.current_job_ids),
        )
    for payload in raw.bridges:
        _index_attempt(
            attempts_by_id,
            _attempt_from_payload(payload, pipeline_id, AttemptKind.BRIDGE, raw.current_bridge_ids),
        )

    def order(attempt: JobAttempt) -> tuple[datetime, int]:
        created = attempt.created_at or datetime.min.replace(tzinfo=UTC)
        return created, attempt.job_id

    attempts = tuple(sorted(attempts_by_id.values(), key=order))
    return PipelineSnapshot(
        instance_base_url=raw.instance_base_url,
        project_id=raw.project_id,
        pipeline_id=pipeline_id,
        iid=int(detail["iid"]) if detail.get("iid") is not None else None,
        name=detail.get("name"),
        ref=detail.get("ref"),
        source=detail.get("source"),
        sha=detail.get("sha"),
        source_status=str(detail.get("status") or ""),
        created_at=created_at,
        started_at=parse_gitlab_timestamp(detail.get("started_at")),
        finished_at=parse_gitlab_timestamp(detail.get("finished_at")),
        updated_at=parse_gitlab_timestamp(detail.get("updated_at")),
        gitlab_duration_s=_optional_float(detail.get("duration")),
        web_url=detail.get("web_url"),
        attempts=attempts,
        completeness=raw.completeness,
        snapshot_error=raw.snapshot_error,
        last_attempt_state=raw.completeness.value,
        last_error=raw.snapshot_error,
    )


class Collector:
    def __init__(self, storage: Storage, client_factory: Callable[[], SupportsGitLab]) -> None:
        self._storage = storage
        self._client_factory = client_factory

    def sync(self, request: SyncRequest, progress: ProgressCallback | None = None) -> SyncResult:
        instance_base_url = canonical_instance_url(request.instance_base_url)
        client = self._client_factory()
        self._notify(progress, 0, 0, "Checking project")
        project = client.check_project(request.project)
        self._storage.upsert_project(
            project,
            instance_base_url,
            request.period.timezone,
            {"source": request.source, "ref": request.ref, "name": request.name},
            fetched_at=datetime.now(UTC),
        )
        start, end = request.period.utc_bounds()
        sync_id = self._storage.record_sync_start(
            instance_base_url,
            project.project_id,
            start,
            end,
            request.period.timezone,
            request.period.start_date,
            request.period.end_date,
        )
        errors: list[str] = []
        warnings: list[str] = []
        discovered = 0
        completed = 0
        state: str | None = None
        try:
            listing = client.list_pipelines(
                request.project,
                start,
                end,
                source=request.source,
                ref=request.ref,
                name=request.name,
            )
            warnings.extend(listing.warnings)
            discovered = len(listing.pipeline_ids)
            self._notify(progress, 0, discovered, "Discovered pipelines")
            if discovered == 0:
                state = "complete"
            else:
                completed, aborted = self._fetch_and_publish(
                    client=client,
                    request=request,
                    instance_base_url=instance_base_url,
                    project_id=project.project_id,
                    pipeline_ids=listing.pipeline_ids,
                    errors=errors,
                    warnings=warnings,
                    progress=progress,
                )
                if aborted:
                    state = "failed"
                elif errors:
                    state = "partial" if completed else "failed"
                elif listing.lossy:
                    state = "partial"
                else:
                    state = "complete"
        except GitLabAuthError as exc:
            errors.append(str(exc))
            state = "failed"
        except GitLabRequestError as exc:
            errors.append(str(exc))
            state = "failed"
        except Exception as exc:
            logger.exception("sync failed unexpectedly for project %s", project.project_id)
            errors.append(f"unexpected sync error: {type(exc).__name__}: {exc}")
            state = "failed"
        finally:
            self._storage.finish_sync(
                sync_id,
                state or "interrupted",
                discovered,
                completed,
                errors,
                datetime.now(UTC),
            )
        return SyncResult(
            sync_id=sync_id,
            instance_base_url=instance_base_url,
            project=project,
            state=state or "interrupted",
            discovered=discovered,
            completed=completed,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _fetch_and_publish(
        self,
        *,
        client: SupportsGitLab,
        request: SyncRequest,
        instance_base_url: str,
        project_id: int,
        pipeline_ids: tuple[int, ...],
        errors: list[str],
        warnings: list[str],
        progress: ProgressCallback | None,
    ) -> tuple[int, bool]:
        completed = 0
        failed = 0
        aborted = False
        workers = max(1, request.workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures: dict[Future[PipelineSnapshot], int] = {
                pool.submit(self._fetch_one, client, request, pipeline_id): pipeline_id
                for pipeline_id in pipeline_ids
            }
            for future in as_completed(futures):
                pipeline_id = futures[future]
                try:
                    snapshot = future.result()
                except GitLabAuthError as exc:
                    errors.append(str(exc))
                    aborted = True
                    for pending in futures:
                        pending.cancel()
                    break
                except Exception as exc:
                    failed += 1
                    message = f"pipeline {pipeline_id}: {type(exc).__name__}: {exc}"
                    errors.append(message)
                    self._storage.record_pipeline_error(
                        instance_base_url, project_id, pipeline_id, message
                    )
                else:
                    if not request.period.contains(snapshot.created_at):
                        warnings.append(
                            f"pipeline {pipeline_id}: detail created_at is outside "
                            "the requested period"
                        )
                    else:
                        self._storage.publish_snapshot(snapshot)
                        completed += 1
                self._notify(
                    progress,
                    completed + failed,
                    len(pipeline_ids),
                    f"Processed {completed + failed}/{len(pipeline_ids)}",
                )
        return completed, aborted

    @staticmethod
    def _fetch_one(
        client: SupportsGitLab, request: SyncRequest, pipeline_id: int
    ) -> PipelineSnapshot:
        raw = client.fetch_snapshot(request.project, pipeline_id)
        return normalize_pipeline(raw)

    @staticmethod
    def _notify(progress: ProgressCallback | None, done: int, total: int, message: str) -> None:
        if progress is not None:
            progress(done, total, message)
