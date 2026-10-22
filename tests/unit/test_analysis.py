from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from glabviz.analysis import (
    ComparisonParams,
    build_pipeline_catalog,
    build_report,
)
from glabviz.domain import (
    MetricPeriod,
    ObjectCategory,
    SnapshotCompleteness,
    SourcePresence,
)
from glabviz.metrics import quantile_cont
from tests.factories import attempt, snapshot

MAY = MetricPeriod(date(2026, 5, 1), date(2026, 5, 31), "UTC")
OCTOBER = MetricPeriod(date(2026, 10, 1), date(2026, 10, 18), "UTC")


def _params(**overrides) -> ComparisonParams:
    values = {"before": MAY, "after": OCTOBER}
    values.update(overrides)
    return ComparisonParams(**values)


def _red(pipeline_id: int, **snapshot_kwargs):
    return snapshot(
        pipeline_id,
        status="failed",
        attempts=(attempt(job_id=pipeline_id * 10, status="failed", name="failed"),),
        **snapshot_kwargs,
    )


def _green(pipeline_id: int, **snapshot_kwargs):
    return snapshot(
        pipeline_id,
        attempts=(attempt(job_id=pipeline_id * 10, name="ok"),),
        **snapshot_kwargs,
    )


def test_pipeline_level_matches_metrics():
    before = [_red(1), _green(2)]
    after = [_green(3)]
    report = build_report(before, after, _params())
    assert report.before.failure_rate.red == 1
    assert report.before.failure_rate.green == 1
    assert report.before.failure_rate.value == pytest.approx(0.5)
    assert report.before.wall.n == 1
    assert report.before.wall.median == pytest.approx(1800)
    assert report.after.failure_rate.value == pytest.approx(0.0)
    assert report.rate_change.delta_pp == pytest.approx(-50)
    assert report.before.observation_count == 2


def test_stage_and_job_levels_use_local_observations():
    snap = snapshot(
        1,
        status="failed",
        attempts=(
            attempt(job_id=1, stage="build", name="compile", status="success"),
            attempt(job_id=2, stage="test", name="unit", status="failed"),
            attempt(job_id=3, stage="test", name="docs", status="success"),
        ),
    )
    report = build_report([snap], [], _params(level="stage", stage="build"))
    assert report.before.failure_rate.red == 0
    assert report.before.failure_rate.green == 1
    assert report.before.wall.median == pytest.approx(300)

    report = build_report([snap], [], _params(level="stage", stage="test"))
    assert report.before.failure_rate.red == 1
    assert report.before.wall.n == 0

    report = build_report([snap], [], _params(level="job", job_stage="test", job_name="docs"))
    assert report.before.failure_rate.red == 0
    assert report.before.failure_rate.green == 1
    assert report.before.wall.median == pytest.approx(300)

    report = build_report([snap], [], _params(level="job", job_stage="test"))
    assert report.before.observation_count == 2


def test_scope_stages_only_affects_pipeline_classification():
    snap = snapshot(
        1,
        status="failed",
        attempts=(
            attempt(job_id=1, stage="deploy", name="deploy", status="failed"),
            attempt(job_id=2, stage="test", name="unit", status="success"),
        ),
    )
    scoped = build_report([snap], [], _params(scope_stages=frozenset({"test"})))
    assert scoped.before.failure_rate.red == 0
    assert scoped.before.failure_rate.outside_fail == 1

    deploy = build_report(
        [snap], [], _params(level="stage", stage="deploy", scope_stages=frozenset({"test"}))
    )
    assert deploy.before.failure_rate.red == 1


def test_retry_in_another_month_stays_in_the_pipeline_period():
    retry = attempt(
        job_id=1,
        status="failed",
        created=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
    )
    snap = snapshot(
        1,
        status="failed",
        created_at=datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
        attempts=(retry,),
    )
    report = build_report(
        [snap],
        [],
        _params(dynamics_metric="failure_rate", granularity="week"),
    )
    points = {point.start_date: point for point in report.dynamics}
    assert points[date(2026, 5, 4)].before == pytest.approx(1.0)
    assert points[date(2026, 10, 5)].before is None


def test_dynamics_emits_empty_buckets_as_gaps():
    first = _green(1, created_at=datetime(2026, 5, 4, 12, 0, tzinfo=UTC))
    second = _green(2, created_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC))
    report = build_report(
        [first, second], [], _params(dynamics_metric="failure_rate", granularity="week")
    )
    points = {point.start_date: point for point in report.dynamics}
    assert points[date(2026, 5, 11)].before is None
    assert points[date(2026, 5, 11)].before_n == 0
    assert points[date(2026, 5, 4)].before == pytest.approx(0.0)


def test_table_rows_are_full_outer_join_with_presence_states():
    before = snapshot(
        1,
        attempts=(attempt(job_id=1, stage="old", name="old job"),),
    )
    after = snapshot(
        2,
        attempts=(attempt(job_id=2, stage="new", name="new job"),),
    )
    report = build_report([before], [after], _params())
    rows = {row.key: row for row in report.stage_rows}
    assert rows[("old",)].presence == "only_before"
    assert rows[("new",)].presence == "only_after"
    assert rows[("old",)].after is None
    assert rows[("new",)].before is None

    job_rows = {row.key: row for row in report.job_rows}
    assert job_rows[("old", "old job")].presence == "only_before"
    assert job_rows[("new", "new job")].presence == "only_after"


def test_quality_counts_incomplete_and_absent_data():
    incomplete = snapshot(
        1,
        completeness=SnapshotCompleteness.INCONSISTENT,
        attempts=(attempt(job_id=1),),
    )
    absent = snapshot(
        2,
        created_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        attempts=(attempt(job_id=2, source_presence=SourcePresence.ABSENT),),
    )
    report = build_report([incomplete, absent], [], _params())
    assert report.quality.before_incomplete == 2
    assert report.quality.before_absent_attempts == 1
    assert report.quality.before_first is not None
    assert report.quality.before_last is not None
    assert report.quality.before_days >= 1


def test_integration_block_counts_allowed_failures_and_honours_the_prefix():
    snap = snapshot(
        1,
        status="failed",
        attempts=(
            attempt(
                job_id=1,
                name="test integration alpha",
                stage="checks",
                status="failed",
                allow_failure=True,
                queued=12.0,
            ),
            attempt(job_id=2, name="lint", stage="checks", status="success"),
        ),
    )
    report = build_report([snap], [], _params())
    assert report.integration_before.pipelines == 1
    assert report.integration_before.red == 1
    assert report.integration_before.pending_known == 1
    assert report.integration_before.pending_expected == 1
    assert report.integration_before.pending.median == pytest.approx(12.0)

    empty = build_report([snap], [], _params(integration_prefix=""))
    assert empty.integration_before.pipelines == 0


def test_pipeline_catalog_uses_classification_and_wall():
    green = _green(1, created_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC))
    red = _red(2, created_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC))
    catalog = build_pipeline_catalog([green, red])
    assert [row.pipeline_id for row in catalog] == [2, 1]
    assert catalog[0].category is ObjectCategory.RED
    assert catalog[0].wall_s is None
    assert catalog[1].category is ObjectCategory.GREEN
    assert catalog[1].wall_s == pytest.approx(1800)


def test_shared_helper_quantile_is_still_used_for_p95():
    first_start = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    second_start = datetime(2026, 5, 2, 10, 0, tzinfo=UTC)
    first = snapshot(
        1,
        started_at=first_start,
        finished_at=first_start + timedelta(minutes=10),
        attempts=(attempt(job_id=1),),
    )
    second = snapshot(
        2,
        started_at=second_start,
        finished_at=second_start + timedelta(minutes=30),
        attempts=(attempt(job_id=2),),
    )
    report = build_report([first, second], [], _params())
    assert report.before.wall.p95 == pytest.approx(quantile_cont([600.0, 1800.0], 0.95))
    assert report.before.wall.p95 != pytest.approx(report.before.wall.mean)


def test_stale_complete_snapshot_is_reported_without_excluding_it():
    snap = replace(
        _green(1),
        last_attempt_state="inconsistent",
        last_error="refresh failed",
    )
    report = build_report([snap], [], _params())
    assert report.quality.before_stale == 1
    assert report.quality.before_incomplete == 0
    assert report.before.failure_rate.green == 1


def test_an_incomplete_snapshot_is_not_counted_as_stale():
    snap = snapshot(
        1,
        completeness=SnapshotCompleteness.INCONSISTENT,
        attempts=(attempt(job_id=1),),
    )
    report = build_report([snap], [], _params())
    assert report.quality.before_incomplete == 1
    assert report.quality.before_stale == 0
