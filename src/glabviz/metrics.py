from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from glabviz.domain import (
    EXECUTED_STATUSES,
    SETTLING_STATUSES,
    TERMINAL_STATUSES,
    AttemptKind,
    ExclusionReason,
    JobAttempt,
    ObjectCategory,
    PipelineSnapshot,
    SnapshotCompleteness,
)


@dataclass(frozen=True)
class WallResult:
    seconds: float | None
    exclusion: ExclusionReason | None = None


@dataclass(frozen=True)
class Stats:
    n: int
    mean: float | None
    median: float | None
    p95: float | None
    maximum: float | None


@dataclass(frozen=True)
class PendingResult:
    known: int
    expected: int
    stats: Stats
    excluded: dict[ExclusionReason, int] = field(default_factory=dict)


def stat_value(stats: Stats, name: str) -> float | None:
    if name == "mean":
        return stats.mean
    if name == "median":
        return stats.median
    if name == "p95":
        return stats.p95
    if name == "max":
        return stats.maximum
    raise ValueError(f"unknown statistic: {name}")


@dataclass(frozen=True)
class FailureRate:
    red: int
    green: int
    outside_fail: int
    excluded: dict[ObjectCategory, int] = field(default_factory=dict)

    @property
    def denominator(self) -> int:
        return self.red + self.green + self.outside_fail

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.red / self.denominator

    @property
    def percent(self) -> float | None:
        value = self.value
        return None if value is None else value * 100


@dataclass(frozen=True)
class PipelineObservation:
    snapshot: PipelineSnapshot
    category: ObjectCategory
    wall: WallResult


@dataclass(frozen=True)
class StageObservation:
    pipeline_id: int
    stage: str
    category: ObjectCategory
    attempts: tuple[JobAttempt, ...]
    executed: bool
    wall: WallResult


@dataclass(frozen=True)
class JobObservation:
    pipeline_id: int
    stage: str
    exact_name: str
    category: ObjectCategory
    attempts: tuple[JobAttempt, ...]
    executed: bool
    wall: WallResult


@dataclass(frozen=True)
class ObjectSummary:
    key: tuple[str, ...]
    observations: int
    executed_observations: int
    failure_rate: FailureRate
    wall: Stats
    wall_excluded: dict[ExclusionReason, int]
    pending: PendingResult


@dataclass(frozen=True)
class IntegrationBlockObservation:
    pipeline_id: int
    red: bool
    wall: float | None
    pending: tuple[float, ...]
    pending_expected: int


@dataclass(frozen=True)
class IntegrationBlockSummary:
    pipelines: int
    red: int
    wall: Stats
    pending: Stats
    pending_known: int
    pending_expected: int

    @property
    def red_share(self) -> float | None:
        if self.pipelines == 0:
            return None
        return self.red / self.pipelines


Observation = PipelineObservation | StageObservation | JobObservation


def _empty_stats() -> Stats:
    return Stats(n=0, mean=None, median=None, p95=None, maximum=None)


def quantile_cont(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize(values: Sequence[float]) -> Stats:
    if not values:
        return _empty_stats()
    ordered = sorted(values)
    return Stats(
        n=len(ordered),
        mean=sum(ordered) / len(ordered),
        median=quantile_cont(ordered, 0.5),
        p95=quantile_cont(ordered, 0.95),
        maximum=ordered[-1],
    )


def counted_failures(attempts: Iterable[JobAttempt]) -> tuple[JobAttempt, ...]:
    return tuple(attempt for attempt in attempts if attempt.counted_failure)


def executed_attempts(attempts: Iterable[JobAttempt]) -> tuple[JobAttempt, ...]:
    return tuple(attempt for attempt in attempts if attempt.is_executed)


def _has_unsettled_attempt(snapshot: PipelineSnapshot, attempts: Sequence[JobAttempt]) -> bool:
    for attempt in attempts:
        if not attempt.is_present:
            continue
        if attempt.source_status in SETTLING_STATUSES:
            return True
        if (
            attempt.source_status == "manual"
            and not attempt.allow_failure
            and snapshot.source_status not in TERMINAL_STATUSES
        ):
            return True
    return False


def classify_pipeline(
    snapshot: PipelineSnapshot, scope_stages: frozenset[str] | None
) -> ObjectCategory:
    attempts = snapshot.attempts
    failures = counted_failures(attempts)
    scoped = (
        failures
        if scope_stages is None
        else tuple(attempt for attempt in failures if attempt.stage in scope_stages)
    )
    status = snapshot.source_status
    if status not in TERMINAL_STATUSES:
        return ObjectCategory.NOT_SETTLED
    if snapshot.completeness is not SnapshotCompleteness.COMPLETE:
        return ObjectCategory.INSUFFICIENT_DATA
    if not snapshot.is_trustworthy:
        return ObjectCategory.RED if scoped else ObjectCategory.INSUFFICIENT_DATA
    if status == "canceled":
        return ObjectCategory.RED if scoped else ObjectCategory.CANCELED
    executed = executed_attempts(attempts)
    if not executed and not failures:
        return ObjectCategory.NO_EXECUTION
    if scoped:
        return ObjectCategory.RED
    if failures:
        return ObjectCategory.OUTSIDE_FAIL
    if status == "failed":
        return ObjectCategory.UNEXPLAINED_FAILED
    if status == "success":
        return ObjectCategory.GREEN
    return ObjectCategory.OTHER


def pipeline_wall(snapshot: PipelineSnapshot) -> WallResult:
    if snapshot.started_at is None or snapshot.finished_at is None:
        return WallResult(None, ExclusionReason.MISSING_TIMESTAMPS)
    seconds = (snapshot.finished_at - snapshot.started_at).total_seconds()
    if seconds < 0:
        return WallResult(None, ExclusionReason.NEGATIVE_INTERVAL)
    return WallResult(seconds)


def build_pipeline_observations(
    snapshots: Iterable[PipelineSnapshot],
    scope_stages: frozenset[str] | None = None,
) -> list[PipelineObservation]:
    observations = []
    for snapshot in snapshots:
        category = classify_pipeline(snapshot, scope_stages)
        wall = (
            pipeline_wall(snapshot)
            if category is ObjectCategory.GREEN
            else WallResult(None, ExclusionReason.NOT_GREEN)
        )
        observations.append(PipelineObservation(snapshot=snapshot, category=category, wall=wall))
    return observations


def _attempt_wall(attempts: Sequence[JobAttempt]) -> WallResult:
    executed = executed_attempts(attempts)
    if not executed:
        return WallResult(None, ExclusionReason.NO_EXECUTED_ATTEMPTS)
    if any(attempt.started_at is None or attempt.finished_at is None for attempt in executed):
        return WallResult(None, ExclusionReason.MISSING_TIMESTAMPS)
    starts = [attempt.started_at for attempt in executed if attempt.started_at is not None]
    finishes = [attempt.finished_at for attempt in executed if attempt.finished_at is not None]
    seconds = (max(finishes) - min(starts)).total_seconds()
    if seconds < 0:
        return WallResult(None, ExclusionReason.NEGATIVE_INTERVAL)
    return WallResult(seconds)


def _stage_category(snapshot: PipelineSnapshot, attempts: Sequence[JobAttempt]) -> ObjectCategory:
    if _has_unsettled_attempt(snapshot, attempts):
        return ObjectCategory.NOT_SETTLED
    failures = counted_failures(attempts)
    if snapshot.completeness is not SnapshotCompleteness.COMPLETE:
        return ObjectCategory.INSUFFICIENT_DATA
    if not snapshot.is_trustworthy:
        return ObjectCategory.RED if failures else ObjectCategory.INSUFFICIENT_DATA
    if failures:
        return ObjectCategory.RED
    if not executed_attempts(attempts):
        return ObjectCategory.NO_EXECUTION
    if any(attempt.is_present and attempt.source_status == "canceled" for attempt in attempts):
        return ObjectCategory.CANCELED
    return ObjectCategory.GREEN


def build_stage_observations(snapshot: PipelineSnapshot) -> list[StageObservation]:
    grouped: dict[str, list[JobAttempt]] = defaultdict(list)
    for attempt in snapshot.attempts:
        grouped[attempt.stage].append(attempt)
    result = []
    for stage, attempts in sorted(grouped.items()):
        category = _stage_category(snapshot, attempts)
        wall = (
            _attempt_wall(attempts)
            if category is ObjectCategory.GREEN
            else WallResult(None, ExclusionReason.NOT_GREEN)
        )
        result.append(
            StageObservation(
                pipeline_id=snapshot.pipeline_id,
                stage=stage,
                category=category,
                attempts=tuple(attempts),
                executed=bool(executed_attempts(attempts)),
                wall=wall,
            )
        )
    return result


def _job_category(snapshot: PipelineSnapshot, attempts: Sequence[JobAttempt]) -> ObjectCategory:
    if _has_unsettled_attempt(snapshot, attempts):
        return ObjectCategory.NOT_SETTLED
    failures = counted_failures(attempts)
    if snapshot.completeness is not SnapshotCompleteness.COMPLETE:
        return ObjectCategory.INSUFFICIENT_DATA
    if not snapshot.is_trustworthy:
        return ObjectCategory.RED if failures else ObjectCategory.INSUFFICIENT_DATA
    if failures:
        return ObjectCategory.RED
    if not executed_attempts(attempts):
        return ObjectCategory.NO_EXECUTION
    return ObjectCategory.GREEN


def _ambiguous_parallel(attempts: Sequence[JobAttempt]) -> bool:
    current = [attempt for attempt in attempts if attempt.is_current_attempt is True]
    return len(current) > 1


def build_job_observations(snapshot: PipelineSnapshot) -> list[JobObservation]:
    grouped: dict[tuple[str, str], list[JobAttempt]] = defaultdict(list)
    for attempt in snapshot.attempts:
        if attempt.kind is AttemptKind.JOB:
            grouped[(attempt.stage, attempt.exact_name)].append(attempt)
    result = []
    for (stage, exact_name), attempts in sorted(grouped.items()):
        if _ambiguous_parallel(attempts):
            category = ObjectCategory.INSUFFICIENT_DATA
            wall = WallResult(None, ExclusionReason.AMBIGUOUS_PARALLEL_JOBS)
        else:
            category = _job_category(snapshot, attempts)
            wall = (
                _attempt_wall(attempts)
                if category is ObjectCategory.GREEN
                else WallResult(None, ExclusionReason.NOT_GREEN)
            )
        result.append(
            JobObservation(
                pipeline_id=snapshot.pipeline_id,
                stage=stage,
                exact_name=exact_name,
                category=category,
                attempts=tuple(attempts),
                executed=bool(executed_attempts(attempts)),
                wall=wall,
            )
        )
    return result


def failure_rate(categories: Iterable[ObjectCategory], include_outside: bool) -> FailureRate:
    counts = Counter(categories)
    outside = counts[ObjectCategory.OUTSIDE_FAIL] if include_outside else 0
    excluded: dict[ObjectCategory, int] = {}
    for category, count in counts.items():
        if category in (ObjectCategory.RED, ObjectCategory.GREEN):
            continue
        if category == ObjectCategory.OUTSIDE_FAIL and include_outside:
            continue
        excluded[category] = count
    return FailureRate(
        red=counts[ObjectCategory.RED],
        green=counts[ObjectCategory.GREEN],
        outside_fail=outside,
        excluded=excluded,
    )


def pipeline_failure_rate(observations: Iterable[PipelineObservation]) -> FailureRate:
    return failure_rate((observation.category for observation in observations), True)


def stage_failure_rate(
    observations: Iterable[StageObservation], stage: str | None = None
) -> FailureRate:
    return failure_rate(
        (
            observation.category
            for observation in observations
            if stage is None or observation.stage == stage
        ),
        False,
    )


def job_failure_rate(observations: Iterable[JobObservation]) -> FailureRate:
    return failure_rate((observation.category for observation in observations), False)


def wall_values(observations: Iterable[Observation]) -> tuple[float, ...]:
    return tuple(
        observation.wall.seconds
        for observation in observations
        if observation.category is ObjectCategory.GREEN and observation.wall.seconds is not None
    )


def _wall_stats(observations: Iterable[Observation]) -> Stats:
    return summarize(wall_values(observations))


def _wall_excluded(
    observations: Iterable[PipelineObservation | StageObservation | JobObservation],
) -> dict[ExclusionReason, int]:
    reasons = Counter(
        observation.wall.exclusion
        for observation in observations
        if observation.category is ObjectCategory.GREEN
        and observation.wall.seconds is None
        and observation.wall.exclusion is not None
    )
    return dict(reasons)


def pipeline_wall_stats(observations: Iterable[PipelineObservation]) -> Stats:
    return _wall_stats(observations)


def pipeline_wall_excluded(
    observations: Iterable[PipelineObservation],
) -> dict[ExclusionReason, int]:
    return _wall_excluded(observations)


def stage_wall_stats(observations: Iterable[StageObservation]) -> Stats:
    return _wall_stats(observations)


def stage_wall_excluded(
    observations: Iterable[StageObservation],
) -> dict[ExclusionReason, int]:
    return _wall_excluded(observations)


def job_wall_stats(observations: Iterable[JobObservation]) -> Stats:
    return _wall_stats(observations)


def job_wall_excluded(observations: Iterable[JobObservation]) -> dict[ExclusionReason, int]:
    return _wall_excluded(observations)


def _pending_from_attempts(attempts: Iterable[JobAttempt]) -> PendingResult:
    values: list[float] = []
    excluded: Counter[ExclusionReason] = Counter()
    expected = 0
    for attempt in attempts:
        if attempt.kind is not AttemptKind.JOB:
            continue
        if not attempt.is_present or attempt.source_status not in EXECUTED_STATUSES:
            continue
        expected += 1
        queued = attempt.queued_duration_s
        if queued is None:
            excluded[ExclusionReason.NULL_QUEUED_DURATION] += 1
        elif queued < 0:
            excluded[ExclusionReason.NEGATIVE_QUEUED_DURATION] += 1
        else:
            values.append(float(queued))
    return PendingResult(
        known=len(values),
        expected=expected,
        stats=summarize(values),
        excluded=dict(excluded),
    )


def pipeline_pending(observations: Iterable[PipelineObservation]) -> PendingResult:
    return _pending_from_attempts(
        attempt
        for observation in observations
        if observation.category is ObjectCategory.GREEN
        for attempt in observation.snapshot.attempts
    )


def stage_pending(observations: Iterable[StageObservation]) -> PendingResult:
    return _pending_from_attempts(
        attempt
        for observation in observations
        if observation.category is ObjectCategory.GREEN
        for attempt in observation.attempts
    )


def job_pending(observations: Iterable[JobObservation]) -> PendingResult:
    return _pending_from_attempts(
        attempt
        for observation in observations
        if observation.category is ObjectCategory.GREEN
        for attempt in observation.attempts
    )


def summarize_stages(
    observations: Iterable[StageObservation],
) -> dict[tuple[str, ...], ObjectSummary]:
    grouped: dict[str, list[StageObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.stage].append(observation)
    summaries: dict[tuple[str, ...], ObjectSummary] = {}
    for stage, group in grouped.items():
        summaries[(stage,)] = ObjectSummary(
            key=(stage,),
            observations=len(group),
            executed_observations=sum(1 for observation in group if observation.executed),
            failure_rate=stage_failure_rate(group),
            wall=_wall_stats(group),
            wall_excluded=_wall_excluded(group),
            pending=stage_pending(group),
        )
    return summaries


def summarize_jobs(
    observations: Iterable[JobObservation],
) -> dict[tuple[str, ...], ObjectSummary]:
    grouped: dict[tuple[str, str], list[JobObservation]] = defaultdict(list)
    for observation in observations:
        grouped[(observation.stage, observation.exact_name)].append(observation)
    summaries: dict[tuple[str, ...], ObjectSummary] = {}
    for key, group in grouped.items():
        summaries[key] = ObjectSummary(
            key=key,
            observations=len(group),
            executed_observations=sum(1 for observation in group if observation.executed),
            failure_rate=job_failure_rate(group),
            wall=_wall_stats(group),
            wall_excluded=_wall_excluded(group),
            pending=job_pending(group),
        )
    return summaries


def build_integration_block_observations(
    snapshots: Iterable[PipelineSnapshot], prefix: str = "test integration"
) -> list[IntegrationBlockObservation]:
    """Raw diagnostic; unlike classification, it counts allow_failure failures."""
    result = []
    for snapshot in snapshots:
        attempts = [
            attempt
            for attempt in snapshot.attempts
            if attempt.kind is AttemptKind.JOB and attempt.exact_name.startswith(prefix)
        ]
        if not attempts:
            continue
        red = any(attempt.observed_failure for attempt in attempts)
        executed_valid = [
            attempt
            for attempt in attempts
            if attempt.is_present
            and attempt.source_status in EXECUTED_STATUSES
            and attempt.started_at is not None
            and attempt.finished_at is not None
        ]
        wall: float | None = None
        if executed_valid:
            starts = [
                attempt.started_at for attempt in executed_valid if attempt.started_at is not None
            ]
            finishes = [
                attempt.finished_at for attempt in executed_valid if attempt.finished_at is not None
            ]
            seconds = (max(finishes) - min(starts)).total_seconds()
            wall = seconds if seconds >= 0 else None
        pending = tuple(
            float(attempt.queued_duration_s)
            for attempt in executed_valid
            if attempt.queued_duration_s is not None and attempt.queued_duration_s >= 0
        )
        result.append(
            IntegrationBlockObservation(
                pipeline_id=snapshot.pipeline_id,
                red=red,
                wall=wall,
                pending=pending,
                pending_expected=len(executed_valid),
            )
        )
    return result


def summarize_integration_block(
    observations: Iterable[IntegrationBlockObservation],
) -> IntegrationBlockSummary:
    items = list(observations)
    return IntegrationBlockSummary(
        pipelines=len(items),
        red=sum(1 for item in items if item.red),
        wall=summarize([item.wall for item in items if item.wall is not None]),
        pending=summarize([value for item in items for value in item.pending]),
        pending_known=sum(len(item.pending) for item in items),
        pending_expected=sum(item.pending_expected for item in items),
    )
