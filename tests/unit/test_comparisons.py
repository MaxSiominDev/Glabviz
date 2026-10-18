from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ci_insights.comparisons import (
    bucket_series,
    compare_rates,
    compare_stats,
    compare_summaries,
    delta,
)
from ci_insights.domain import MetricPeriod, ObjectCategory
from ci_insights.metrics import (
    JobObservation,
    WallResult,
    build_pipeline_observations,
    summarize,
    summarize_jobs,
)
from tests.factories import snapshot


def _job_observation(name: str, seconds: float | None) -> JobObservation:
    return JobObservation(
        pipeline_id=1,
        stage="test",
        exact_name=name,
        category=ObjectCategory.GREEN,
        attempts=(),
        executed=True,
        wall=WallResult(seconds),
    )


def test_m14_mean_median_p95_and_relative_change():
    comparison = compare_stats([100, 200], [50, 100])
    assert comparison.before.mean == pytest.approx(150)
    assert comparison.after.mean == pytest.approx(75)
    assert comparison.before.median == pytest.approx(150)
    assert comparison.after.median == pytest.approx(75)
    assert comparison.before.p95 == pytest.approx(195)
    assert comparison.after.p95 == pytest.approx(97.5)
    assert comparison.mean.absolute == pytest.approx(-75)
    assert comparison.mean.relative_percent == pytest.approx(-50)


def test_m15_period_mean_uses_raw_observations():
    values = [100] + [10] * 9
    assert summarize(values).mean == pytest.approx(19)


def test_m16_rate_change_is_percentage_points_and_relative():
    change = compare_rates(0.70, 0.05)
    assert change.delta_pp == pytest.approx(-65)
    assert change.relative_percent == pytest.approx(-92.857142857, rel=1e-9)


def test_m17_zero_baseline_and_empty_samples_do_not_produce_infinity():
    assert delta(0, 5).relative_percent is None
    assert compare_rates(0, 0.5).relative_percent is None
    empty = compare_stats([], [])
    assert empty.before.mean is None
    assert empty.after.mean is None
    assert empty.mean.absolute is None
    assert empty.mean.relative_percent is None


def test_m21_objects_present_only_on_one_side_are_not_zero():
    before = summarize_jobs([_job_observation("old", 60)])
    after = summarize_jobs([_job_observation("new", 30)])
    comparisons = {comparison.key: comparison for comparison in compare_summaries(before, after)}
    old = comparisons[("test", "old")]
    assert old.presence == "only_before"
    assert old.after is None
    assert old.mean_wall.absolute is None

    new = comparisons[("test", "new")]
    assert new.presence == "only_after"
    assert new.before is None
    assert new.mean_wall.absolute is None

    both_before = summarize_jobs([_job_observation("same", None)])
    both_after = summarize_jobs([_job_observation("same", None)])
    both = {comparison.key: comparison for comparison in compare_summaries(both_before, both_after)}
    same = both[("test", "same")]
    assert same.presence == "both"
    assert same.mean_wall.absolute is None
    assert same.before is not None
    assert same.before.wall.n == 0


def test_m25_period_boundaries_follow_dst_and_midnight():
    new_york = MetricPeriod(date(2026, 3, 7), date(2026, 3, 9), "America/New_York")
    start, end = new_york.utc_bounds()
    assert start == datetime(2026, 3, 7, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 10, 4, 0, tzinfo=UTC)
    assert (end - start).total_seconds() == 71 * 3600

    santiago = MetricPeriod(date(2026, 9, 5), date(2026, 9, 7), "America/Santiago")
    santiago_start, santiago_end = santiago.utc_bounds()
    assert santiago_start == datetime(2026, 9, 5, 4, 0, tzinfo=UTC)
    assert santiago_end == datetime(2026, 9, 8, 3, 0, tzinfo=UTC)
    assert (santiago_end - santiago_start).total_seconds() == 71 * 3600
    assert santiago.contains(datetime(2026, 9, 6, 4, 30, tzinfo=UTC))
    assert not santiago.contains(datetime(2026, 9, 8, 3, 0, tzinfo=UTC))


def test_empty_buckets_are_explicit_gaps():
    period = MetricPeriod(date(2026, 5, 1), date(2026, 5, 31), "UTC")
    observations = build_pipeline_observations(
        [
            snapshot(1, created_at=datetime(2026, 5, 4, 12, 0, tzinfo=UTC)),
            snapshot(2, created_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC)),
        ]
    )
    buckets = bucket_series(observations, period, "week")
    present = [bucket.start_date for bucket in buckets if bucket.present]
    assert present == [date(2026, 5, 4), date(2026, 5, 18)]
    gap = [bucket for bucket in buckets if bucket.start_date == date(2026, 5, 11)]
    assert len(gap) == 1
    assert gap[0].present is False
    assert gap[0].observations == ()
