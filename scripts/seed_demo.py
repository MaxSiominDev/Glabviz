#!/usr/bin/env python3
"""Write a deterministic synthetic database for offline review and browser smoke tests."""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from glabviz.domain import (
    AttemptKind,
    JobAttempt,
    PipelineSnapshot,
    ProjectInfo,
    SnapshotCompleteness,
    SourcePresence,
)
from glabviz.storage import Storage

INSTANCE = "https://gitlab.example.com"
PROJECT_ID = 1
PROJECT_PATH = "group/demo"
START = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)


def _attempt(
    pipeline_id: int,
    job_id: int,
    name: str,
    stage: str,
    status: str,
    created: datetime,
    *,
    start: datetime | None,
    finish: datetime | None,
    queued: float | None,
    allow_failure: bool = False,
    kind: AttemptKind = AttemptKind.JOB,
    is_current: bool | None = True,
) -> JobAttempt:
    duration = (finish - start).total_seconds() if start and finish else None
    return JobAttempt(
        job_id=job_id,
        pipeline_id=pipeline_id,
        exact_name=name,
        stage=stage,
        kind=kind,
        source_status=status,
        allow_failure=allow_failure,
        observed_failure=status == "failed",
        is_current_attempt=is_current,
        created_at=created,
        started_at=start,
        finished_at=finish,
        gitlab_duration_s=duration,
        queued_duration_s=queued,
        runner_id=1,
        web_url=f"{INSTANCE}/jobs/{job_id}",
        failure_reason="script_failure" if status == "failed" else None,
        source_presence=SourcePresence.PRESENT,
    )


def _pipeline(
    pipeline_id: int,
    created: datetime,
    jobs: list[JobAttempt],
    *,
    status: str,
    completeness: SnapshotCompleteness = SnapshotCompleteness.COMPLETE,
    snapshot_error: str | None = None,
    extra_minutes: int = 0,
) -> PipelineSnapshot:
    starts = [job.started_at for job in jobs if job.started_at is not None]
    finishes = [job.finished_at for job in jobs if job.finished_at is not None]
    started = min(starts) if starts else None
    finished = max(finishes) + timedelta(minutes=extra_minutes) if finishes else None
    return PipelineSnapshot(
        instance_base_url=INSTANCE,
        project_id=PROJECT_ID,
        pipeline_id=pipeline_id,
        iid=pipeline_id,
        name="pipeline",
        ref=f"refs/merge-requests/{pipeline_id}/head",
        source="merge_request_event",
        sha=f"sha{pipeline_id:04d}",
        source_status=status,
        created_at=created,
        started_at=started,
        finished_at=finished,
        updated_at=finished,
        gitlab_duration_s=((finished - started).total_seconds() if started and finished else None),
        web_url=f"{INSTANCE}/pipelines/{pipeline_id}",
        attempts=tuple(jobs),
        completeness=completeness,
        snapshot_error=snapshot_error,
        last_attempt_state=completeness.value,
        last_error=snapshot_error,
    )


class _Builder:
    def __init__(self, pipeline_id: int, created: datetime, pending_minutes: float) -> None:
        self.pipeline_id = pipeline_id
        self.jobs: list[JobAttempt] = []
        self.job_id = pipeline_id * 100
        self.cursor = created + timedelta(minutes=1)
        self.pending_minutes = pending_minutes
        self.failed = False

    def add(
        self,
        name: str,
        stage: str,
        duration_minutes: float,
        *,
        status: str = "success",
        allow_failure: bool = False,
        is_current: bool = True,
    ) -> None:
        self.job_id += 1
        start = self.cursor
        finish = start + timedelta(minutes=duration_minutes)
        self.jobs.append(
            _attempt(
                self.pipeline_id,
                self.job_id,
                name,
                stage,
                status,
                created=start,
                start=start,
                finish=finish,
                queued=self.pending_minutes * 60,
                allow_failure=allow_failure,
                is_current=is_current,
            )
        )
        self.cursor = finish
        if status == "failed" and not allow_failure:
            self.failed = True


def _build_period(
    rng: random.Random,
    first_id: int,
    month: datetime,
    count: int,
    *,
    failure_rate: float,
    base_minutes: int,
    pending_minutes: float,
) -> list[PipelineSnapshot]:
    pipelines = []
    for index in range(count):
        pipeline_id = first_id + index
        created = month + timedelta(days=index, hours=9, minutes=rng.randint(0, 50))
        builder = _Builder(pipeline_id, created, pending_minutes)
        builder.add("build", "build", base_minutes * 0.5)
        builder.add("lint", "checks", base_minutes * 0.3)
        builder.add("test unit", "checks", base_minutes * 0.8)

        broken = rng.random() < failure_rate and index != 9
        if index == 6:
            builder.add(
                "test unit",
                "checks",
                base_minutes * 0.8,
                status="failed",
                is_current=False,
            )
            builder.add("test unit", "checks", base_minutes * 0.8)
        elif broken:
            allowed = index % 3 == 0
            builder.add(
                "test integration alpha",
                "checks",
                base_minutes * 1.2,
                status="failed",
                allow_failure=allowed,
            )
        else:
            builder.add("test integration alpha", "checks", base_minutes * 1.2)
            builder.add("test integration beta", "checks", base_minutes * 1.1)

        if index == 9:
            builder.add("e2e suite", "e2e", 43 * 60 + 48)
        if not builder.failed:
            builder.add("deploy", "deploy", base_minutes * 0.4)

        status = "failed" if builder.failed else "success"
        extra = 0
        if index == 2:
            status = "canceled"
            extra = 1
        if index == 4:
            builder.jobs = []
            status = "skipped"
        pipelines.append(
            _pipeline(
                pipeline_id,
                created,
                builder.jobs,
                status=status,
                extra_minutes=extra,
            )
        )
    return pipelines


def _bridge_pipeline(pipeline_id: int) -> PipelineSnapshot:
    created = START + timedelta(days=6, hours=11)
    started = created + timedelta(minutes=1)
    job = _attempt(
        pipeline_id,
        9901,
        "build",
        "build",
        "success",
        created=started,
        start=started,
        finish=started + timedelta(minutes=4),
        queued=3.0,
    )
    bridge = _attempt(
        pipeline_id,
        9902,
        "run_e2e",
        "e2e_test",
        "failed",
        created=started + timedelta(minutes=5),
        start=started + timedelta(minutes=5),
        finish=started + timedelta(minutes=41),
        queued=15.0,
        kind=AttemptKind.BRIDGE,
    )
    return _pipeline(pipeline_id, created, [job, bridge], status="failed")


def seed(directory: Path, pipelines: int) -> int:
    rng = random.Random(7)
    storage = Storage.open(directory)
    try:
        storage.upsert_project(
            ProjectInfo(
                project_id=PROJECT_ID,
                path=PROJECT_PATH,
                name="Demo project",
                default_branch="main",
            ),
            INSTANCE,
            "UTC",
            {"source": "merge_request_event", "ref": None, "name": None},
        )
        may = _build_period(
            rng,
            1000,
            datetime(2026, 5, 4, tzinfo=UTC),
            max(1, pipelines // 2),
            failure_rate=0.45,
            base_minutes=22,
            pending_minutes=2.5,
        )
        october = _build_period(
            rng,
            2000,
            datetime(2026, 10, 1, tzinfo=UTC),
            max(1, pipelines // 2),
            failure_rate=0.2,
            base_minutes=9,
            pending_minutes=0.4,
        )
        bridge = _bridge_pipeline(3001)
        orphan = _attempt(
            3002,
            300201,
            "orphan job",
            "checks",
            "created",
            datetime(2026, 10, 6, 10, 0, tzinfo=UTC),
            start=None,
            finish=None,
            queued=None,
        )
        incomplete = _pipeline(
            3002,
            datetime(2026, 10, 6, 10, 0, tzinfo=UTC),
            [orphan],
            status="running",
            completeness=SnapshotCompleteness.INCONSISTENT,
            snapshot_error="synthetic incomplete snapshot",
        )
        snapshots = [*may, *october, bridge, incomplete]
        for snapshot in snapshots:
            storage.publish_snapshot(snapshot)
    finally:
        storage.close()
    return len(snapshots)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--pipelines", type=int, default=20)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    database = args.data_dir / "glabviz.duckdb"
    if database.exists() and not args.force:
        print(
            f"{database} already exists; pass --force to add the demo data anyway",
            file=sys.stderr,
        )
        return 1
    count = seed(args.data_dir, max(2, args.pipelines))
    print(f"seeded {count} pipelines into {args.data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
