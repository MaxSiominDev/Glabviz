from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from glabviz.analysis_config import (
    ComparisonConfig,
    GanttConfig,
    PeriodConfig,
    PipelineFilters,
    ProjectRef,
    SavedAnalysisConfig,
)
from glabviz.domain import ProjectInfo, SnapshotCompleteness
from glabviz.storage import Storage
from tests.factories import attempt, snapshot

INSTANCE = "https://gitlab.example.com"
PROJECT_ID = 1


def _job(
    job_id: int,
    pipeline_id: int,
    name: str,
    stage: str,
    start: datetime,
    finish: datetime,
    *,
    status: str = "success",
    queued: float = 1.0,
):
    return attempt(
        job_id=job_id,
        pipeline_id=pipeline_id,
        name=name,
        stage=stage,
        status=status,
        start=start,
        finish=finish,
        queued=queued,
    )


def seed_project(
    directory: Path,
    *,
    project_id: int = PROJECT_ID,
    path: str = "group/project",
    local_filter: dict[str, object] | None = None,
    with_pipelines: bool = True,
) -> None:
    store = Storage.open(directory)
    try:
        store.upsert_project(
            ProjectInfo(
                project_id=project_id,
                path=path,
                name="Project",
                default_branch="main",
            ),
            INSTANCE,
            "UTC",
            {"source": "merge_request_event"} if local_filter is None else local_filter,
        )
        if not with_pipelines:
            return
        may = datetime(2026, 5, 10, 10, 0, tzinfo=UTC)
        store.publish_snapshot(
            snapshot(
                1,
                status="failed",
                created_at=may,
                started_at=may,
                finished_at=may + timedelta(minutes=30),
                attempts=(
                    _job(11, 1, "lint", "checks", may, may + timedelta(minutes=5), queued=2),
                    _job(
                        12,
                        1,
                        "test integration alpha",
                        "checks",
                        may + timedelta(minutes=6),
                        may + timedelta(minutes=20),
                        status="failed",
                        queued=180,
                    ),
                    _job(
                        13,
                        1,
                        "deploy",
                        "deploy",
                        may + timedelta(minutes=21),
                        may + timedelta(minutes=25),
                    ),
                ),
            )
        )
        october = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
        store.publish_snapshot(
            snapshot(
                2,
                created_at=october,
                started_at=october,
                finished_at=october + timedelta(minutes=15),
                attempts=(
                    _job(
                        21,
                        2,
                        "lint",
                        "checks",
                        october,
                        october + timedelta(minutes=3),
                    ),
                    _job(
                        22,
                        2,
                        "test integration alpha",
                        "checks",
                        october + timedelta(minutes=4),
                        october + timedelta(minutes=10),
                        queued=10,
                    ),
                    _job(
                        23,
                        2,
                        "deploy",
                        "deploy",
                        october + timedelta(minutes=11),
                        october + timedelta(minutes=14),
                    ),
                ),
            )
        )
        long = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
        store.publish_snapshot(
            snapshot(
                3,
                created_at=long,
                started_at=long,
                finished_at=long + timedelta(hours=2),
                attempts=(
                    _job(31, 3, "e2e suite", "e2e", long, long + timedelta(hours=2)),
                    _job(
                        32,
                        3,
                        "unit",
                        "test",
                        long,
                        long + timedelta(minutes=5),
                    ),
                ),
            )
        )
        incomplete = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
        store.publish_snapshot(
            snapshot(
                4,
                status="running",
                created_at=incomplete,
                started_at=incomplete,
                finished_at=None,
                completeness=SnapshotCompleteness.INCONSISTENT,
                snapshot_error="synthetic",
                attempts=(
                    attempt(
                        job_id=41,
                        pipeline_id=4,
                        name="orphan",
                        stage="test",
                        status="created",
                        start=None,
                        finish=None,
                    ),
                ),
            )
        )
    finally:
        store.close()


def seed_saved_analysis(
    directory: Path,
    name: str = "Smoke saved",
    primary: int | None = 1,
    secondary: int | None = 3,
    dynamics_metric: str = "failure_rate",
    project_id: int = PROJECT_ID,
) -> str:
    store = Storage.open(directory)
    try:
        config = SavedAnalysisConfig(
            project=ProjectRef(INSTANCE, project_id),
            filters=PipelineFilters(source="merge_request_event"),
            before=PeriodConfig(date(2026, 5, 1), date(2026, 5, 31), "UTC"),
            after=PeriodConfig(date(2026, 10, 1), date(2026, 10, 18), "UTC"),
            comparison=ComparisonConfig(level="pipeline", dynamics_metric=dynamics_metric),  # type: ignore[arg-type]
            gantt=GanttConfig(primary_pipeline_id=primary, secondary_pipeline_id=secondary),
        )
        return store.save_analysis(name, config).analysis_id
    finally:
        store.close()
