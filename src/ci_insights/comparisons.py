from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from ci_insights.domain import MetricPeriod, PipelineSnapshot
from ci_insights.metrics import (
    ObjectSummary,
    PipelineObservation,
    Stats,
    summarize,
)

Granularity = Literal["day", "week", "month"]
Presence = Literal["both", "only_before", "only_after"]


@dataclass(frozen=True)
class Delta:
    absolute: float | None
    relative_percent: float | None


@dataclass(frozen=True)
class RateDelta:
    before: float | None
    after: float | None
    delta_pp: float | None
    relative_percent: float | None


@dataclass(frozen=True)
class StatsComparison:
    before: Stats
    after: Stats
    mean: Delta
    median: Delta
    p95: Delta


@dataclass(frozen=True)
class ObjectComparison:
    key: tuple[str, ...]
    presence: Presence
    before: ObjectSummary | None
    after: ObjectSummary | None
    rate: RateDelta
    mean_wall: Delta
    median_wall: Delta
    p95_wall: Delta


@dataclass(frozen=True)
class MetricBucket:
    start_date: date
    observations: tuple[PipelineObservation, ...]

    @property
    def present(self) -> bool:
        return bool(self.observations)


def delta(before: float | None, after: float | None) -> Delta:
    if before is None or after is None:
        return Delta(absolute=None, relative_percent=None)
    absolute = after - before
    relative = None if before == 0 else 100 * absolute / before
    return Delta(absolute=absolute, relative_percent=relative)


def compare_rates(before: float | None, after: float | None) -> RateDelta:
    if before is None or after is None:
        return RateDelta(before=before, after=after, delta_pp=None, relative_percent=None)
    difference = after - before
    relative = None if before == 0 else 100 * difference / before
    return RateDelta(
        before=before,
        after=after,
        delta_pp=100 * difference,
        relative_percent=relative,
    )


def compare_stats(before_values: Sequence[float], after_values: Sequence[float]) -> StatsComparison:
    before = summarize(before_values)
    after = summarize(after_values)
    return StatsComparison(
        before=before,
        after=after,
        mean=delta(before.mean, after.mean),
        median=delta(before.median, after.median),
        p95=delta(before.p95, after.p95),
    )


def select_snapshots(
    snapshots: Iterable[PipelineSnapshot], period: MetricPeriod
) -> list[PipelineSnapshot]:
    return [snapshot for snapshot in snapshots if period.contains(snapshot.created_at)]


def compare_summaries(
    before: Mapping[tuple[str, ...], ObjectSummary],
    after: Mapping[tuple[str, ...], ObjectSummary],
) -> list[ObjectComparison]:
    comparisons = []
    for key in sorted(set(before) | set(after)):
        before_summary = before.get(key)
        after_summary = after.get(key)
        if before_summary is not None and after_summary is not None:
            presence: Presence = "both"
        elif before_summary is not None:
            presence = "only_before"
        else:
            presence = "only_after"
        comparisons.append(
            ObjectComparison(
                key=key,
                presence=presence,
                before=before_summary,
                after=after_summary,
                rate=compare_rates(
                    before_summary.failure_rate.value if before_summary else None,
                    after_summary.failure_rate.value if after_summary else None,
                ),
                mean_wall=delta(
                    before_summary.wall.mean if before_summary else None,
                    after_summary.wall.mean if after_summary else None,
                ),
                median_wall=delta(
                    before_summary.wall.median if before_summary else None,
                    after_summary.wall.median if after_summary else None,
                ),
                p95_wall=delta(
                    before_summary.wall.p95 if before_summary else None,
                    after_summary.wall.p95 if after_summary else None,
                ),
            )
        )
    return comparisons


def _bucket_key(day: date, granularity: Granularity) -> date:
    if granularity == "day":
        return day
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _next_bucket(start: date, granularity: Granularity) -> date:
    if granularity == "day":
        return start + timedelta(days=1)
    if granularity == "week":
        return start + timedelta(days=7)
    if start.month == 12:
        return date(start.year + 1, 1, 1)
    return date(start.year, start.month + 1, 1)


def bucket_series(
    observations: Iterable[PipelineObservation],
    period: MetricPeriod,
    granularity: Granularity,
) -> list[MetricBucket]:
    grouped: dict[date, list[PipelineObservation]] = {}
    for observation in observations:
        created_at = observation.snapshot.created_at
        if not period.contains(created_at):
            continue
        local_day = created_at.astimezone(period.zone).date()
        grouped.setdefault(_bucket_key(local_day, granularity), []).append(observation)
    first = _bucket_key(period.start_date, granularity)
    last = _bucket_key(period.end_date, granularity)
    buckets = []
    current = first
    while current <= last:
        buckets.append(
            MetricBucket(start_date=current, observations=tuple(grouped.get(current, ())))
        )
        current = _next_bucket(current, granularity)
    return buckets


def bucket_local_day(value: datetime, period: MetricPeriod) -> date:
    return value.astimezone(period.zone).date()
