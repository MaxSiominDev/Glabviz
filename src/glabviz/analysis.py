from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from glabviz.analysis_config import (
    AnalysisLevel,
    DynamicsMetric,
    Granularity,
)
from glabviz.comparisons import (
    BucketGroup,
    ObjectComparison,
    RateDelta,
    StatsComparison,
    bucket_snapshots,
    compare_rates,
    compare_stats,
    compare_summaries,
)
from glabviz.domain import (
    ExclusionReason,
    MetricPeriod,
    ObjectCategory,
    PipelineSnapshot,
    SnapshotCompleteness,
)
from glabviz.metrics import (
    FailureRate,
    IntegrationBlockSummary,
    JobObservation,
    PendingResult,
    PipelineObservation,
    StageObservation,
    Stats,
    build_integration_block_observations,
    build_job_observations,
    build_pipeline_observations,
    build_stage_observations,
    classify_pipeline,
    job_failure_rate,
    job_pending,
    job_wall_excluded,
    job_wall_stats,
    pipeline_failure_rate,
    pipeline_pending,
    pipeline_wall,
    pipeline_wall_excluded,
    pipeline_wall_stats,
    stage_failure_rate,
    stage_pending,
    stage_wall_excluded,
    stage_wall_stats,
    stat_value,
    summarize_integration_block,
    summarize_jobs,
    summarize_stages,
    wall_values,
)


@dataclass(frozen=True)
class ComparisonParams:
    before: MetricPeriod
    after: MetricPeriod
    level: AnalysisLevel = "pipeline"
    stage: str | None = None
    job_stage: str | None = None
    job_name: str | None = None
    scope_stages: frozenset[str] | None = None
    dynamics_metric: DynamicsMetric = "failure_rate"
    granularity: Granularity = "week"
    integration_prefix: str = "test integration"


@dataclass(frozen=True)
class PeriodFacts:
    period: MetricPeriod
    label: str
    observation_count: int
    failure_rate: FailureRate
    wall: Stats
    wall_values: tuple[float, ...]
    wall_excluded: dict[ExclusionReason, int]
    pending: PendingResult


@dataclass(frozen=True)
class DynamicsPoint:
    start_date: date
    before: float | None
    after: float | None
    before_n: int
    after_n: int


@dataclass(frozen=True)
class Quality:
    before_snapshots: int
    after_snapshots: int
    before_incomplete: int
    after_incomplete: int
    before_stale: int
    after_stale: int
    before_absent_attempts: int
    after_absent_attempts: int
    before_excluded: dict[ObjectCategory, int]
    after_excluded: dict[ObjectCategory, int]
    before_first: datetime | None
    before_last: datetime | None
    after_first: datetime | None
    after_last: datetime | None
    before_days: int
    after_days: int


@dataclass(frozen=True)
class PipelineCatalogRow:
    pipeline_id: int
    iid: int | None
    created_at: datetime
    source_status: str
    category: ObjectCategory
    wall_s: float | None
    completeness: SnapshotCompleteness
    web_url: str | None


@dataclass(frozen=True)
class ComparisonReport:
    params: ComparisonParams
    before: PeriodFacts
    after: PeriodFacts
    rate_change: RateDelta
    wall_change: StatsComparison
    stage_rows: tuple[ObjectComparison, ...]
    job_rows: tuple[ObjectComparison, ...]
    dynamics: tuple[DynamicsPoint, ...]
    integration_before: IntegrationBlockSummary
    integration_after: IntegrationBlockSummary
    quality: Quality


@dataclass(frozen=True)
class _LevelData:
    pipelines: tuple[PipelineObservation, ...]
    stages: tuple[StageObservation, ...]
    jobs: tuple[JobObservation, ...]


@dataclass(frozen=True)
class _MetricFacts:
    observation_count: int
    failure_rate: FailureRate
    wall: Stats
    wall_values: tuple[float, ...]
    wall_excluded: dict[ExclusionReason, int]
    pending: PendingResult


def _build_level_data(
    snapshots: Sequence[PipelineSnapshot], scope: frozenset[str] | None
) -> _LevelData:
    stages: list[StageObservation] = []
    jobs: list[JobObservation] = []
    for snapshot in snapshots:
        stages.extend(build_stage_observations(snapshot))
        jobs.extend(build_job_observations(snapshot))
    return _LevelData(
        pipelines=tuple(build_pipeline_observations(snapshots, scope)),
        stages=tuple(stages),
        jobs=tuple(jobs),
    )


def _selected_stages(data: _LevelData, params: ComparisonParams) -> tuple[StageObservation, ...]:
    if params.stage is None:
        return data.stages
    return tuple(item for item in data.stages if item.stage == params.stage)


def _selected_jobs(data: _LevelData, params: ComparisonParams) -> tuple[JobObservation, ...]:
    return tuple(
        item
        for item in data.jobs
        if (params.job_stage is None or item.stage == params.job_stage)
        and (params.job_name is None or item.exact_name == params.job_name)
    )


def _metric_facts(data: _LevelData, params: ComparisonParams) -> _MetricFacts:
    if params.level == "pipeline":
        return _MetricFacts(
            observation_count=len(data.pipelines),
            failure_rate=pipeline_failure_rate(data.pipelines),
            wall=pipeline_wall_stats(data.pipelines),
            wall_values=wall_values(data.pipelines),
            wall_excluded=pipeline_wall_excluded(data.pipelines),
            pending=pipeline_pending(data.pipelines),
        )
    if params.level == "stage":
        selected = _selected_stages(data, params)
        return _MetricFacts(
            observation_count=len(selected),
            failure_rate=stage_failure_rate(selected),
            wall=stage_wall_stats(selected),
            wall_values=wall_values(selected),
            wall_excluded=stage_wall_excluded(selected),
            pending=stage_pending(selected),
        )
    selected_jobs = _selected_jobs(data, params)
    return _MetricFacts(
        observation_count=len(selected_jobs),
        failure_rate=job_failure_rate(selected_jobs),
        wall=job_wall_stats(selected_jobs),
        wall_values=wall_values(selected_jobs),
        wall_excluded=job_wall_excluded(selected_jobs),
        pending=job_pending(selected_jobs),
    )


def _period_facts(
    period: MetricPeriod,
    data: _LevelData,
    params: ComparisonParams,
) -> PeriodFacts:
    facts = _metric_facts(data, params)
    return PeriodFacts(
        period=period,
        label=period.label(),
        observation_count=facts.observation_count,
        failure_rate=facts.failure_rate,
        wall=facts.wall,
        wall_values=facts.wall_values,
        wall_excluded=facts.wall_excluded,
        pending=facts.pending,
    )


def _pick_metric(facts: _MetricFacts, metric: DynamicsMetric) -> tuple[float | None, int]:
    if metric == "failure_rate":
        return facts.failure_rate.value, facts.failure_rate.denominator
    if metric.startswith("wall_"):
        return stat_value(facts.wall, metric.removeprefix("wall_")), facts.wall.n
    return stat_value(facts.pending.stats, metric.removeprefix("pending_")), facts.pending.known


def _bucket_values(
    buckets: Iterable[BucketGroup[PipelineSnapshot]],
    params: ComparisonParams,
    scope: frozenset[str] | None,
    metric: DynamicsMetric,
) -> dict[date, tuple[float | None, int]]:
    result: dict[date, tuple[float | None, int]] = {}
    for bucket in buckets:
        if not bucket.present:
            result[bucket.start_date] = (None, 0)
            continue
        data = _build_level_data(bucket.items, scope)
        result[bucket.start_date] = _pick_metric(_metric_facts(data, params), metric)
    return result


def build_dynamics(
    before_snapshots: Sequence[PipelineSnapshot],
    after_snapshots: Sequence[PipelineSnapshot],
    params: ComparisonParams,
) -> tuple[DynamicsPoint, ...]:
    scope = params.scope_stages or None
    before = _bucket_values(
        bucket_snapshots(before_snapshots, params.before, params.granularity),
        params,
        scope,
        params.dynamics_metric,
    )
    after = _bucket_values(
        bucket_snapshots(after_snapshots, params.after, params.granularity),
        params,
        scope,
        params.dynamics_metric,
    )
    points = []
    for start_date in sorted(set(before) | set(after)):
        before_value, before_n = before.get(start_date, (None, 0))
        after_value, after_n = after.get(start_date, (None, 0))
        points.append(
            DynamicsPoint(
                start_date=start_date,
                before=before_value,
                after=after_value,
                before_n=before_n,
                after_n=after_n,
            )
        )
    return tuple(points)


def _integration(snapshots: Sequence[PipelineSnapshot], prefix: str) -> IntegrationBlockSummary:
    text = prefix.strip()
    if not text:
        return summarize_integration_block(())
    return summarize_integration_block(build_integration_block_observations(snapshots, text))


def _local_bounds(
    snapshots: Sequence[PipelineSnapshot], period: MetricPeriod
) -> tuple[datetime | None, datetime | None, int]:
    values = [snapshot.created_at for snapshot in snapshots]
    if not values:
        return None, None, 0
    first = min(values)
    last = max(values)
    first_day = first.astimezone(period.zone).date()
    last_day = last.astimezone(period.zone).date()
    return first, last, (last_day - first_day).days + 1


def _quality(
    before_snapshots: Sequence[PipelineSnapshot],
    after_snapshots: Sequence[PipelineSnapshot],
    params: ComparisonParams,
    before_facts: PeriodFacts,
    after_facts: PeriodFacts,
) -> Quality:
    before_first, before_last, before_days = _local_bounds(before_snapshots, params.before)
    after_first, after_last, after_days = _local_bounds(after_snapshots, params.after)
    return Quality(
        before_snapshots=len(before_snapshots),
        after_snapshots=len(after_snapshots),
        before_incomplete=sum(1 for item in before_snapshots if not item.is_trustworthy),
        after_incomplete=sum(1 for item in after_snapshots if not item.is_trustworthy),
        before_stale=sum(1 for item in before_snapshots if item.is_stale),
        after_stale=sum(1 for item in after_snapshots if item.is_stale),
        before_absent_attempts=sum(
            1 for item in before_snapshots for attempt in item.attempts if not attempt.is_present
        ),
        after_absent_attempts=sum(
            1 for item in after_snapshots for attempt in item.attempts if not attempt.is_present
        ),
        before_excluded=dict(before_facts.failure_rate.excluded),
        after_excluded=dict(after_facts.failure_rate.excluded),
        before_first=before_first,
        before_last=before_last,
        after_first=after_first,
        after_last=after_last,
        before_days=before_days,
        after_days=after_days,
    )


def build_report(
    before_snapshots: Sequence[PipelineSnapshot],
    after_snapshots: Sequence[PipelineSnapshot],
    params: ComparisonParams,
) -> ComparisonReport:
    """Build a report from snapshots already selected for each period.

    Callers query each period with its own bounds. Dynamics filters internally only
    because it groups raw observations into buckets.
    """
    scope = params.scope_stages or None
    before_data = _build_level_data(before_snapshots, scope)
    after_data = _build_level_data(after_snapshots, scope)
    before_facts = _period_facts(params.before, before_data, params)
    after_facts = _period_facts(params.after, after_data, params)
    return ComparisonReport(
        params=params,
        before=before_facts,
        after=after_facts,
        rate_change=compare_rates(before_facts.failure_rate.value, after_facts.failure_rate.value),
        wall_change=compare_stats(before_facts.wall_values, after_facts.wall_values),
        stage_rows=tuple(
            compare_summaries(
                summarize_stages(before_data.stages), summarize_stages(after_data.stages)
            )
        ),
        job_rows=tuple(
            compare_summaries(summarize_jobs(before_data.jobs), summarize_jobs(after_data.jobs))
        ),
        dynamics=build_dynamics(before_snapshots, after_snapshots, params),
        integration_before=_integration(before_snapshots, params.integration_prefix),
        integration_after=_integration(after_snapshots, params.integration_prefix),
        quality=_quality(before_snapshots, after_snapshots, params, before_facts, after_facts),
    )


def build_pipeline_catalog(
    snapshots: Iterable[PipelineSnapshot],
) -> list[PipelineCatalogRow]:
    rows = []
    for snapshot in snapshots:
        category = classify_pipeline(snapshot, None)
        wall = pipeline_wall(snapshot)
        rows.append(
            PipelineCatalogRow(
                pipeline_id=snapshot.pipeline_id,
                iid=snapshot.iid,
                created_at=snapshot.created_at,
                source_status=snapshot.source_status,
                category=category,
                wall_s=wall.seconds if category is ObjectCategory.GREEN else None,
                completeness=snapshot.completeness,
                web_url=snapshot.web_url,
            )
        )
    rows.sort(key=lambda row: (row.created_at, row.pipeline_id), reverse=True)
    return rows
