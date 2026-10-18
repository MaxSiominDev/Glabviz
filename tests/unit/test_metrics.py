from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from ci_insights.domain import (
    AttemptKind,
    ExclusionReason,
    MetricPeriod,
    ObjectCategory,
    SnapshotCompleteness,
    SourcePresence,
)
from ci_insights.metrics import (
    build_job_observations,
    build_pipeline_observations,
    build_stage_observations,
    classify_pipeline,
    job_pending,
    pipeline_failure_rate,
    pipeline_pending,
    stage_failure_rate,
    stage_wall_stats,
)
from tests.factories import BASE, attempt, snapshot


def _jobs(snap):
    return {observation.exact_name: observation for observation in build_job_observations(snap)}


def _stages(snap):
    return {observation.stage: observation for observation in build_stage_observations(snap)}


def test_m01_failure_rate_counts_pipelines_not_attempts():
    def make(pipeline_id, failed):
        attempts = [
            attempt(
                job_id=pipeline_id * 10 + 1,
                status="failed" if failed else "success",
                is_current=not failed,
            )
        ]
        if failed:
            attempts.append(
                attempt(
                    job_id=pipeline_id * 10 + 2,
                    status="success",
                    is_current=True,
                )
            )
        return snapshot(pipeline_id, attempts=tuple(attempts))

    observations = build_pipeline_observations(
        [make(1, True), make(2, False), make(3, True), make(4, False)]
    )
    rate = pipeline_failure_rate(observations)
    assert (rate.red, rate.green, rate.outside_fail) == (2, 2, 0)
    assert rate.value == pytest.approx(0.5)


def test_m02_failed_deploy_does_not_taint_successful_integration_stage():
    integration = attempt(job_id=1, name="integration", stage="integration", status="success")
    deploy = attempt(job_id=2, name="deploy", stage="deploy", status="failed")
    snap = snapshot(1, status="failed", attempts=(integration, deploy))
    assert classify_pipeline(snap, None) is ObjectCategory.RED
    integration_stage = _stages(snap)["integration"]
    assert integration_stage.category is ObjectCategory.GREEN
    assert stage_wall_stats([integration_stage]).n == 1


def test_m03_failed_then_successful_integration_job_keeps_stage_red():
    failed = attempt(
        job_id=1,
        name="integration",
        stage="integration",
        status="failed",
        start=BASE,
        finish=BASE + timedelta(minutes=2),
        is_current=False,
    )
    success = attempt(
        job_id=2,
        name="integration",
        stage="integration",
        status="success",
        start=BASE + timedelta(minutes=3),
        finish=BASE + timedelta(minutes=6),
        is_current=True,
    )
    snap = snapshot(1, attempts=(failed, success))
    stage = _stages(snap)["integration"]
    assert stage.category is ObjectCategory.RED
    assert stage_wall_stats([stage]).n == 0


def test_m04_clean_job_has_wall_even_when_neighbor_fails():
    clean = attempt(
        job_id=1,
        name="clean",
        stage="test",
        status="success",
        start=BASE,
        finish=BASE + timedelta(minutes=5),
    )
    broken = attempt(
        job_id=2,
        name="broken",
        stage="test",
        status="failed",
        start=BASE,
        finish=BASE + timedelta(minutes=5),
    )
    snap = snapshot(1, status="failed", attempts=(clean, broken))
    job_map = _jobs(snap)
    assert job_map["clean"].category is ObjectCategory.GREEN
    assert job_map["clean"].wall.seconds == 300
    stage = _stages(snap)["test"]
    assert stage.category is ObjectCategory.RED
    assert stage_wall_stats([stage]).n == 0


def test_m05_allow_failure_is_inverted_does_not_redden_and_stays_in_cohorts():
    review = attempt(
        job_id=1,
        name="review",
        stage="lint",
        status="failed",
        allow_failure=True,
        queued=5.0,
    )
    build = attempt(job_id=2, name="build", stage="build", status="success", queued=3.0)
    snap = snapshot(1, attempts=(review, build))
    assert classify_pipeline(snap, None) is ObjectCategory.GREEN
    for stage in build_stage_observations(snap):
        assert stage.category is ObjectCategory.GREEN
    pending = pipeline_pending(build_pipeline_observations([snap]))
    assert (pending.known, pending.expected) == (2, 2)


def test_m06_canceled_without_failure_is_not_green():
    snap = snapshot(1, status="canceled", attempts=(attempt(job_id=1, status="canceled"),))
    observations = build_pipeline_observations([snap])
    assert observations[0].category is ObjectCategory.CANCELED
    rate = pipeline_failure_rate(observations)
    assert rate.denominator == 0
    assert rate.value is None
    assert rate.excluded[ObjectCategory.CANCELED] == 1


def test_m07_canceled_after_failure_is_red():
    failed = attempt(job_id=1, status="failed", is_current=False)
    canceled = attempt(job_id=2, status="canceled", is_current=True)
    snap = snapshot(1, status="canceled", attempts=(failed, canceled))
    observations = build_pipeline_observations([snap])
    assert observations[0].category is ObjectCategory.RED
    assert pipeline_failure_rate(observations).red == 1


def test_m08_skipped_or_absent_stage_is_not_a_zero_duration_success():
    skipped = attempt(
        job_id=1,
        name="integration",
        stage="integration",
        status="skipped",
        start=None,
    )
    build = attempt(job_id=2, name="build", stage="build", status="success")
    snap = snapshot(1, attempts=(skipped, build))
    stage_map = _stages(snap)
    assert stage_map["integration"].category is ObjectCategory.NO_EXECUTION
    assert stage_map["integration"].wall.seconds is None
    assert stage_wall_stats([stage_map["integration"]]).n == 0
    assert "deploy" not in stage_map


def test_m09_manual_jobs_do_not_create_failure_or_zero_duration():
    build = attempt(job_id=1, name="build", stage="build", status="success")
    optional = attempt(
        job_id=2,
        name="deploy",
        stage="deploy",
        status="manual",
        allow_failure=True,
        start=None,
    )
    optional_snap = snapshot(1, attempts=(build, optional))
    assert classify_pipeline(optional_snap, None) is ObjectCategory.GREEN
    deploy_stage = _stages(optional_snap)["deploy"]
    assert deploy_stage.category is ObjectCategory.NO_EXECUTION
    assert deploy_stage.wall.seconds is None

    required = attempt(
        job_id=3,
        name="deploy",
        stage="deploy",
        status="manual",
        allow_failure=False,
        start=None,
    )
    required_snap = snapshot(2, status="manual", attempts=(build, required))
    assert classify_pipeline(required_snap, None) is ObjectCategory.NOT_SETTLED
    assert _stages(required_snap)["deploy"].category is ObjectCategory.NOT_SETTLED


def test_m10_stage_wall_is_first_start_to_last_finish():
    first = attempt(
        job_id=1,
        name="a",
        stage="test",
        start=BASE,
        finish=BASE + timedelta(minutes=15),
    )
    second = attempt(
        job_id=2,
        name="b",
        stage="test",
        start=BASE + timedelta(minutes=5),
        finish=BASE + timedelta(minutes=20),
    )
    snap = snapshot(1, attempts=(first, second))
    assert _stages(snap)["test"].wall.seconds == 1200


def test_m11_pause_inside_stage_is_included():
    first = attempt(
        job_id=1,
        name="a",
        stage="test",
        start=BASE,
        finish=BASE + timedelta(minutes=10),
    )
    second = attempt(
        job_id=2,
        name="b",
        stage="test",
        start=BASE + timedelta(minutes=15),
        finish=BASE + timedelta(minutes=20),
    )
    snap = snapshot(1, attempts=(first, second))
    assert _stages(snap)["test"].wall.seconds == 1200


def test_m12_pipeline_wall_uses_started_and_finished_not_duration():
    snap = snapshot(
        1,
        created_at=BASE - timedelta(minutes=10),
        started_at=BASE,
        finished_at=BASE + timedelta(minutes=40),
        gitlab_duration_s=9999.0,
        attempts=(attempt(job_id=1),),
    )
    observation = build_pipeline_observations([snap])[0]
    assert observation.wall.seconds == 2400


def test_m13_missing_timestamp_and_negative_interval_are_excluded_with_reason():
    missing = snapshot(1, started_at=None, finished_at=BASE, attempts=(attempt(job_id=1),))
    negative = snapshot(
        2,
        started_at=BASE + timedelta(minutes=10),
        finished_at=BASE,
        attempts=(attempt(job_id=2),),
    )
    observations = build_pipeline_observations([missing, negative])
    assert observations[0].wall.seconds is None
    assert observations[0].wall.exclusion is ExclusionReason.MISSING_TIMESTAMPS
    assert observations[1].wall.seconds is None
    assert observations[1].wall.exclusion is ExclusionReason.NEGATIVE_INTERVAL

    broken = attempt(job_id=3, stage="test", start=BASE, finish=None)
    stage = _stages(snapshot(3, attempts=(broken,)))["test"]
    assert stage.wall.exclusion is ExclusionReason.MISSING_TIMESTAMPS


def test_m18_pending_null_is_not_zero():
    snap = snapshot(
        1,
        attempts=(
            attempt(job_id=1, name="a", queued=None),
            attempt(job_id=2, name="b", queued=0.0),
            attempt(job_id=3, name="c", queued=60.0),
        ),
    )
    pending = pipeline_pending(build_pipeline_observations([snap]))
    assert (pending.known, pending.expected) == (2, 3)
    assert pending.stats.mean == pytest.approx(30)
    assert pending.stats.median == pytest.approx(30)
    assert pending.stats.p95 == pytest.approx(57)


def test_m19_pending_uses_queued_duration_not_created_to_start():
    snap = snapshot(
        1,
        attempts=(
            attempt(
                job_id=1,
                created=BASE - timedelta(hours=1),
                start=BASE,
                queued=2.0,
            ),
        ),
    )
    pending = pipeline_pending(build_pipeline_observations([snap]))
    assert pending.stats.mean == pytest.approx(2)


def test_m20_stage_failure_rate_denominator_is_stage_runs():
    before = [
        snapshot(
            1,
            status="failed",
            attempts=(attempt(job_id=1, stage="test", status="failed"),),
        ),
        snapshot(
            2,
            status="failed",
            attempts=(attempt(job_id=2, stage="test", status="failed"),),
        ),
    ]
    after = [
        snapshot(
            10 + index,
            status="failed",
            attempts=(attempt(job_id=100 + index, stage="test", status="failed"),),
        )
        for index in range(8)
    ]
    before_observations = [
        observation for snap in before for observation in build_stage_observations(snap)
    ]
    after_observations = [
        observation for snap in after for observation in build_stage_observations(snap)
    ]
    assert stage_failure_rate(before_observations).value == pytest.approx(1.0)
    assert stage_failure_rate(after_observations).value == pytest.approx(1.0)


def test_m22_same_sha_is_two_observations():
    first = snapshot(1, sha="same", attempts=(attempt(job_id=1),))
    second = snapshot(2, sha="same", attempts=(attempt(job_id=2),))
    observations = build_pipeline_observations([first, second])
    assert len(observations) == 2


def test_m23_retry_in_october_stays_in_may_period():
    retry = attempt(
        job_id=1,
        status="failed",
        created=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    )
    snap = snapshot(
        1,
        status="failed",
        created_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        attempts=(retry,),
    )
    may = MetricPeriod(date(2026, 5, 1), date(2026, 5, 31), "UTC")
    october = MetricPeriod(date(2026, 10, 1), date(2026, 10, 31), "UTC")
    assert may.contains(snap.created_at)
    assert not october.contains(snap.created_at)
    assert classify_pipeline(snap, None) is ObjectCategory.RED


def test_m24_multiple_successful_attempts_are_one_job_observation():
    first = attempt(
        job_id=1,
        name="unit",
        stage="test",
        start=BASE,
        finish=BASE + timedelta(minutes=5),
        is_current=False,
    )
    second = attempt(
        job_id=2,
        name="unit",
        stage="test",
        start=BASE + timedelta(minutes=10),
        finish=BASE + timedelta(minutes=20),
        is_current=True,
    )
    snap = snapshot(1, attempts=(first, second))
    observations = build_job_observations(snap)
    assert len(observations) == 1
    assert observations[0].wall.seconds == 1200


def test_m26_job_pending_is_available_in_a_red_pipeline():
    clean = attempt(job_id=1, name="clean", stage="clean", status="success", queued=7.0)
    broken = attempt(job_id=2, name="broken", stage="other", status="failed")
    snap = snapshot(1, status="failed", attempts=(clean, broken))
    clean_job = _jobs(snap)["clean"]
    assert clean_job.category is ObjectCategory.GREEN
    pending = job_pending([clean_job])
    assert (pending.known, pending.expected) == (1, 1)
    assert pending.stats.mean == pytest.approx(7.0)


def test_bridge_failure_reddens_pipeline_and_stage_but_is_not_a_runner_job():
    job = attempt(job_id=1, name="build", stage="build", status="success")
    bridge = attempt(
        job_id=2,
        name="run_e2e_tests",
        stage="e2e_test",
        status="failed",
        kind=AttemptKind.BRIDGE,
    )
    snap = snapshot(1, status="failed", attempts=(job, bridge))
    assert classify_pipeline(snap, None) is ObjectCategory.RED
    assert _stages(snap)["e2e_test"].category is ObjectCategory.RED
    assert "run_e2e_tests" not in _jobs(snap)

    allowed_bridge = attempt(
        job_id=3,
        name="run_e2e_tests",
        stage="e2e_test",
        status="failed",
        allow_failure=True,
        kind=AttemptKind.BRIDGE,
    )
    allowed_snap = snapshot(2, attempts=(job, allowed_bridge))
    assert classify_pipeline(allowed_snap, None) is ObjectCategory.GREEN


def test_duplicate_current_job_names_are_excluded_from_job_metrics():
    first = attempt(job_id=1, name="test unit", stage="test", is_current=True)
    second = attempt(job_id=2, name="test unit", stage="test", is_current=True)
    snap = snapshot(1, attempts=(first, second))
    job_observations = build_job_observations(snap)
    assert len(job_observations) == 1
    assert job_observations[0].category is ObjectCategory.INSUFFICIENT_DATA
    assert job_observations[0].wall.exclusion is ExclusionReason.AMBIGUOUS_PARALLEL_JOBS
    stage = _stages(snap)["test"]
    assert stage.category is ObjectCategory.GREEN
    assert stage.wall.seconds is not None


def test_never_complete_snapshot_is_excluded_from_failure_rate():
    failed = attempt(job_id=1, status="failed")
    snap = snapshot(
        1,
        attempts=(failed,),
        completeness=SnapshotCompleteness.INCONSISTENT,
        snapshot_error="changed during fetch",
    )
    observations = build_pipeline_observations([snap])
    assert observations[0].category is ObjectCategory.INSUFFICIENT_DATA
    assert _stages(snap)["build"].category is ObjectCategory.INSUFFICIENT_DATA
    assert _jobs(snap)["build"].category is ObjectCategory.INSUFFICIENT_DATA
    rate = pipeline_failure_rate(observations)
    assert rate.denominator == 0
    assert rate.value is None
    assert rate.excluded[ObjectCategory.INSUFFICIENT_DATA] == 1


def test_complete_source_discrepancy_failure_still_reddens():
    absent = attempt(
        job_id=1,
        status="failed",
        source_presence=SourcePresence.ABSENT,
    )
    snap = snapshot(1, attempts=(absent,))
    assert snap.source_discrepancy is True
    assert classify_pipeline(snap, None) is ObjectCategory.RED
    assert pipeline_failure_rate(build_pipeline_observations([snap])).red == 1


def test_unexplained_failed_is_excluded_from_failure_rate():
    success = attempt(job_id=1, status="success")
    snap = snapshot(1, status="failed", attempts=(success,))
    observations = build_pipeline_observations([snap])
    assert observations[0].category is ObjectCategory.UNEXPLAINED_FAILED
    rate = pipeline_failure_rate(observations)
    assert rate.denominator == 0
    assert rate.excluded[ObjectCategory.UNEXPLAINED_FAILED] == 1


def test_stage_scope_marks_failures_outside_the_cut():
    inside = attempt(job_id=1, stage="lint_and_test", status="failed")
    outside = attempt(job_id=2, stage="deploy", status="failed")
    inside_snap = snapshot(1, status="failed", attempts=(inside,))
    outside_snap = snapshot(2, status="failed", attempts=(outside,))
    scope = frozenset({"lint_and_test"})
    assert classify_pipeline(inside_snap, scope) is ObjectCategory.RED
    assert classify_pipeline(outside_snap, scope) is ObjectCategory.OUTSIDE_FAIL
    rate = pipeline_failure_rate(build_pipeline_observations([inside_snap, outside_snap], scope))
    assert (rate.red, rate.green, rate.outside_fail) == (1, 0, 1)
    assert rate.value == pytest.approx(0.5)


def test_bridge_is_excluded_from_pending_and_extends_stage_wall():
    job = attempt(
        job_id=1,
        name="job",
        stage="e2e",
        status="success",
        start=BASE,
        finish=BASE + timedelta(minutes=5),
        queued=3.0,
    )
    bridge = attempt(
        job_id=2,
        name="trigger",
        stage="e2e",
        status="success",
        kind=AttemptKind.BRIDGE,
        start=BASE + timedelta(minutes=5),
        finish=BASE + timedelta(minutes=10),
        queued=100.0,
    )
    snap = snapshot(1, attempts=(job, bridge))
    assert _stages(snap)["e2e"].wall.seconds == 600
    pending = pipeline_pending(build_pipeline_observations([snap]))
    assert (pending.known, pending.expected) == (1, 1)
    assert pending.stats.mean == pytest.approx(3.0)
