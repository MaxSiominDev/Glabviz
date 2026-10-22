from __future__ import annotations

from typing import cast
from zoneinfo import ZoneInfo

import streamlit as st

from glabviz.analysis import ComparisonParams, ComparisonReport, PeriodFacts, build_report
from glabviz.analysis_config import (
    DYNAMICS_METRICS,
    GRANULARITIES,
    LEVELS,
    PENDING_METRICS,
    WALL_METRICS,
    AnalysisLevel,
    ChartText,
    DynamicsMetric,
    Granularity,
    PendingMetric,
    WallMetric,
)
from glabviz.charts import (
    annotate_note,
    delta_caption,
    distribution_chart,
    duration_chart,
    dynamics_chart,
    failure_rate_chart,
    pending_chart,
    rate_delta_caption,
)
from glabviz.comparisons import ObjectComparison, delta
from glabviz.domain import AttemptKind, PipelineSnapshot
from glabviz.formatting import (
    format_datetime,
    format_percent,
    format_rate,
    format_seconds,
)
from glabviz.runtime import Runtime
from glabviz.storage import Coverage, StoredProject
from glabviz.ui import keys
from glabviz.ui.analysis_state import clear_saved_selection, render_save_controls
from glabviz.ui.common import (
    coverage_caption,
    default_periods,
    parse_period,
    periods_overlap,
    pick_project,
    project_value,
    zone_or_none,
)
from glabviz.ui.export import (
    render_chart_export,
    render_export_settings,
    render_figure,
)
from glabviz.ui.loaders import load_period

_METRIC_LABELS = {
    "failure_rate": "Failure rate",
    "wall_mean": "Wall mean",
    "wall_median": "Wall P50",
    "wall_p95": "Wall P95",
    "wall_max": "Wall max",
    "pending_mean": "Pending mean",
    "pending_median": "Pending P50",
    "pending_p95": "Pending P95",
}
_LEVEL_NOUN = {"pipeline": "pipeline runs", "stage": "stage runs", "job": "job runs"}


def _sanitize_select(key: str, options: list[str], default: str) -> None:
    if st.session_state.get(key) not in options:
        st.session_state.pop(key, None)
        st.session_state[key] = default


def _sanitize_multiselect(key: str, options: list[str]) -> None:
    current = st.session_state.get(key)
    if not isinstance(current, list):
        st.session_state[key] = list(options)
        return
    filtered = [value for value in current if value in options]
    if current and not filtered:
        filtered = list(options)
    st.session_state[key] = filtered


def _stage_names(*groups: list[PipelineSnapshot]) -> list[str]:
    return sorted(
        {attempt.stage for group in groups for snapshot in group for attempt in snapshot.attempts}
    )


def _job_options(stage: str, *groups: list[PipelineSnapshot]) -> list[tuple[str, str]]:
    pairs = {
        (attempt.stage, attempt.exact_name)
        for group in groups
        for snapshot in group
        for attempt in snapshot.attempts
        if attempt.kind is AttemptKind.JOB and (not stage or attempt.stage == stage)
    }
    return sorted(pairs)


def _reset_for_project(
    selected: StoredProject, coverage: Coverage, *, project_changed: bool
) -> None:
    local_filter = selected.local_filter if isinstance(selected.local_filter, dict) else {}
    if "source" in local_filter:
        default_source = local_filter.get("source") or ""
    else:
        default_source = "merge_request_event"
    st.session_state[keys.C_SOURCE] = default_source
    st.session_state[keys.C_REF] = local_filter.get("ref") or ""
    st.session_state[keys.C_NAME] = local_filter.get("name") or ""
    if project_changed or keys.C_TIMEZONE not in st.session_state:
        st.session_state[keys.C_TIMEZONE] = selected.default_timezone or "UTC"
    zone_name = str(st.session_state.get(keys.C_TIMEZONE) or "UTC")
    zone = zone_or_none(zone_name) or ZoneInfo("UTC")
    defaults = default_periods(coverage, zone)
    st.session_state[keys.C_A_START] = defaults[0]
    st.session_state[keys.C_A_END] = defaults[1]
    st.session_state[keys.C_B_START] = defaults[2]
    st.session_state[keys.C_B_END] = defaults[3]
    st.session_state[keys.C_LEVEL] = "pipeline"
    st.session_state[keys.C_STAGE] = ""
    st.session_state[keys.C_JOB_STAGE] = ""
    st.session_state[keys.C_JOB] = ""
    st.session_state[keys.C_SCOPE_ENABLED] = False
    st.session_state[keys.C_SCOPE_STAGES] = []
    st.session_state[keys.COMPARE_PREVIOUS_PROJECT] = project_value(selected)
    if project_changed:
        clear_saved_selection()
        for key in (
            keys.GANTT_STATE,
            keys.GANTT_PENDING_HIDDEN,
            keys.GANTT_PREVIOUS_PROJECT,
            keys.G_PIPELINE_A,
            keys.G_PIPELINE_B,
        ):
            st.session_state.pop(key, None)


def _facts_column(title: str, facts: PeriodFacts) -> None:
    st.subheader(title)
    st.caption(facts.label)
    st.metric("Observations", facts.observation_count)
    st.metric("Failure rate", format_rate(facts.failure_rate))
    st.caption(
        f"red {facts.failure_rate.red}, green {facts.failure_rate.green}, "
        f"outside {facts.failure_rate.outside_fail}; "
        f"excluded {sum(facts.failure_rate.excluded.values())}"
    )
    st.metric("Wall mean", format_seconds(facts.wall.mean))
    st.metric("Wall P50", format_seconds(facts.wall.median))
    st.metric("Wall P95", format_seconds(facts.wall.p95))
    st.caption(f"wall n={facts.wall.n}; excluded {sum(facts.wall_excluded.values())}")
    st.metric("Pending mean", format_seconds(facts.pending.stats.mean))
    st.metric("Pending P50", format_seconds(facts.pending.stats.median))
    st.metric("Pending P95", format_seconds(facts.pending.stats.p95))
    st.caption(f"pending {facts.pending.known}/{facts.pending.expected} known")


def _change_column(report: ComparisonReport) -> None:
    st.subheader("Change")
    st.metric("Observations", report.after.observation_count - report.before.observation_count)
    st.metric(
        "Failure rate",
        rate_delta_caption(report.rate_change.delta_pp, report.rate_change.relative_percent),
    )
    st.metric(
        "Wall mean",
        delta_caption(
            report.wall_change.mean.absolute,
            report.wall_change.mean.relative_percent,
        ),
    )
    st.metric(
        "Wall P50",
        delta_caption(
            report.wall_change.median.absolute,
            report.wall_change.median.relative_percent,
        ),
    )
    st.metric(
        "Wall P95",
        delta_caption(
            report.wall_change.p95.absolute,
            report.wall_change.p95.relative_percent,
        ),
    )
    pending_mean = delta(report.before.pending.stats.mean, report.after.pending.stats.mean)
    pending_median = delta(report.before.pending.stats.median, report.after.pending.stats.median)
    pending_p95 = delta(report.before.pending.stats.p95, report.after.pending.stats.p95)
    st.metric(
        "Pending mean",
        delta_caption(pending_mean.absolute, pending_mean.relative_percent),
    )
    st.metric(
        "Pending P50",
        delta_caption(pending_median.absolute, pending_median.relative_percent),
    )
    st.metric(
        "Pending P95",
        delta_caption(pending_p95.absolute, pending_p95.relative_percent),
    )


def _period_note(report: ComparisonReport, extra: str = "") -> str:
    note = f"A: {report.before.label}; B: {report.after.label}"
    if extra:
        note = f"{note}; {extra}"
    return note


def _table_rows(rows: tuple[ObjectComparison, ...]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        before = row.before
        after = row.after
        result.append(
            {
                "object": " :: ".join(row.key),
                "presence": {
                    "both": "both",
                    "only_before": "only A",
                    "only_after": "only B",
                }[row.presence],
                "n A": before.observations if before else None,
                "rate A %": before.failure_rate.percent if before else None,
                "n B": after.observations if after else None,
                "rate B %": after.failure_rate.percent if after else None,
                "delta pp": row.rate.delta_pp,
                "wall mean A s": before.wall.mean if before else None,
                "wall mean B s": after.wall.mean if after else None,
                "delta mean s": row.mean_wall.absolute,
                "wall P50 A s": before.wall.median if before else None,
                "wall P50 B s": after.wall.median if after else None,
                "delta P50 s": row.median_wall.absolute,
                "wall P95 A s": before.wall.p95 if before else None,
                "wall P95 B s": after.wall.p95 if after else None,
                "delta P95 s": row.p95_wall.absolute,
                "pending P50 A s": before.pending.stats.median if before else None,
                "pending P50 B s": after.pending.stats.median if after else None,
                "delta pending P50 s": row.pending_median.absolute,
                "pending P95 A s": before.pending.stats.p95 if before else None,
                "pending P95 B s": after.pending.stats.p95 if after else None,
                "delta pending P95 s": row.pending_p95.absolute,
            }
        )
    return result


def _render_table(report: ComparisonReport, level: str, all_stages: list[str]) -> None:
    if keys.C_TABLE not in st.session_state:
        st.session_state[keys.C_TABLE] = "Stages" if level == "pipeline" else "Jobs"
    kind = st.radio("Table", ["Stages", "Jobs"], horizontal=True, key=keys.C_TABLE)
    rows = report.stage_rows if kind == "Stages" else report.job_rows
    data = _table_rows(rows)
    if kind == "Jobs":
        left, right = st.columns(2)
        search = left.text_input("Search job", key=keys.C_TABLE_SEARCH)
        stage_options = [""] + all_stages
        _sanitize_select(keys.C_TABLE_STAGE, stage_options, "")
        stage_filter = right.selectbox(
            "Stage filter",
            stage_options,
            format_func=lambda value: value or "(all stages)",
            key=keys.C_TABLE_STAGE,
        )
        if search.strip():
            needle = search.strip().lower()
            data = [row for row in data if needle in str(row["object"]).lower()]
        if stage_filter:
            data = [row for row in data if str(row["object"]).startswith(f"{stage_filter} ::")]
    if not data:
        st.info("No observations for the selected table.")
        return
    st.dataframe(
        data,
        hide_index=True,
        width="stretch",
        column_config={
            "rate A %": st.column_config.NumberColumn(format="%.1f"),
            "rate B %": st.column_config.NumberColumn(format="%.1f"),
            "delta pp": st.column_config.NumberColumn(format="%.1f"),
            "wall mean A s": st.column_config.NumberColumn(format="%.0f"),
            "wall mean B s": st.column_config.NumberColumn(format="%.0f"),
            "delta mean s": st.column_config.NumberColumn(format="%.0f"),
            "wall P50 A s": st.column_config.NumberColumn(format="%.0f"),
            "wall P50 B s": st.column_config.NumberColumn(format="%.0f"),
            "delta P50 s": st.column_config.NumberColumn(format="%.0f"),
            "wall P95 A s": st.column_config.NumberColumn(format="%.0f"),
            "wall P95 B s": st.column_config.NumberColumn(format="%.0f"),
            "delta P95 s": st.column_config.NumberColumn(format="%.0f"),
            "pending P50 A s": st.column_config.NumberColumn(format="%.0f"),
            "pending P50 B s": st.column_config.NumberColumn(format="%.0f"),
            "delta pending P50 s": st.column_config.NumberColumn(format="%.0f"),
            "pending P95 A s": st.column_config.NumberColumn(format="%.0f"),
            "pending P95 B s": st.column_config.NumberColumn(format="%.0f"),
            "delta pending P95 s": st.column_config.NumberColumn(format="%.0f"),
        },
    )


def _render_quality(
    report: ComparisonReport,
    coverage: Coverage,
    zone: ZoneInfo,
    level: str,
    scope_stages: frozenset[str] | None,
) -> None:
    quality = report.quality
    with st.expander("What was counted", expanded=False):
        st.markdown(
            "**Red**: at least one counted failed attempt in scope. "
            "`allow_failure=true` failures never redden an object. "
            "**Green**: completed with executed attempts and no counted failures. "
            "**Outside-fail**: failures only outside the selected stage scope."
        )
        st.markdown(
            "**Wall-clock**: pipeline = finished - started; stage = latest finish - earliest "
            "start of its executed jobs; job = first start to last finish of its attempts. "
            "Only green objects, with exclusion reasons kept."
        )
        st.markdown(
            "**Pending**: queued duration of job attempts in green objects. "
            "NULL is not zero; known and expected counts are shown separately."
        )
        st.markdown(
            f"**Level**: {_LEVEL_NOUN[level]}. "
            + (
                "Pipeline classification is limited to the selected stages; "
                "stage and job metrics stay local to their own attempts."
                if scope_stages
                else "No stage scope limit; all stages count for pipeline classification."
            )
        )
        st.write(
            f"A: {quality.before_snapshots} pipelines, {quality.before_incomplete} incomplete, "
            f"{quality.before_stale} stale, "
            f"{quality.before_absent_attempts} attempts missing from source, "
            f"data {format_datetime(quality.before_first, zone)} .. "
            f"{format_datetime(quality.before_last, zone)} "
            f"({quality.before_days} days)."
        )
        st.write(
            f"B: {quality.after_snapshots} pipelines, {quality.after_incomplete} incomplete, "
            f"{quality.after_stale} stale, "
            f"{quality.after_absent_attempts} attempts missing from source, "
            f"data {format_datetime(quality.after_first, zone)} .. "
            f"{format_datetime(quality.after_last, zone)} "
            f"({quality.after_days} days)."
        )
        st.write(
            "Excluded categories A: "
            + (
                ", ".join(f"{key.value}={value}" for key, value in quality.before_excluded.items())
                or "none"
            )
        )
        st.write(
            "Excluded categories B: "
            + (
                ", ".join(f"{key.value}={value}" for key, value in quality.after_excluded.items())
                or "none"
            )
        )
        st.caption(coverage_caption(coverage, zone))
        st.caption(
            "Limits: only the API-visible history is known; hidden jobs do not change metrics; "
            "no critical path is inferred."
        )


def _render_integration(report: ComparisonReport, prefix: str) -> None:
    with st.expander("Integration block (diagnostic)", expanded=False):
        st.caption(
            "Counts every failed attempt, including allow_failure=true. "
            "This is a raw diagnostic block, not the red/green rule."
        )
        before = report.integration_before
        after = report.integration_after
        if not prefix.strip():
            st.info("Enter a job name prefix to enable this block.")
            return
        rows = [
            {
                "metric": "Pipelines with the block",
                "A": f"{before.pipelines}/{report.quality.before_snapshots}",
                "B": f"{after.pipelines}/{report.quality.after_snapshots}",
            },
            {
                "metric": "Red share",
                "A": f"{before.red} ({format_percent(before.red_share)})",
                "B": f"{after.red} ({format_percent(after.red_share)})",
            },
            {
                "metric": "Wall P50",
                "A": format_seconds(before.wall.median),
                "B": format_seconds(after.wall.median),
            },
            {
                "metric": "Wall P95",
                "A": format_seconds(before.wall.p95),
                "B": format_seconds(after.wall.p95),
            },
            {
                "metric": "Pending P50",
                "A": format_seconds(before.pending.median),
                "B": format_seconds(after.pending.median),
            },
            {
                "metric": "Pending P95",
                "A": format_seconds(before.pending.p95),
                "B": format_seconds(after.pending.p95),
            },
            {
                "metric": "Pending known/expected",
                "A": f"{before.pending_known}/{before.pending_expected}",
                "B": f"{after.pending_known}/{after.pending_expected}",
            },
        ]
        st.dataframe(rows, hide_index=True, width="stretch")


def render_compare_page(runtime: Runtime) -> None:
    projects = runtime.storage.projects()
    previous_project = st.session_state.get(keys.C_PROJECT)
    selected = pick_project(projects, keys.C_PROJECT)
    if selected is None:
        return
    coverage = runtime.storage.coverage(selected.instance_base_url, selected.project_id)
    current_project = project_value(selected)
    if st.session_state.get(keys.COMPARE_PREVIOUS_PROJECT) != current_project:
        _reset_for_project(selected, coverage, project_changed=previous_project != current_project)

    if keys.C_TIMEZONE not in st.session_state:
        st.session_state[keys.C_TIMEZONE] = selected.default_timezone or "UTC"
    zone_name = st.text_input("Timezone", key=keys.C_TIMEZONE)
    zone = zone_or_none(zone_name)
    if zone is None:
        st.error("Timezone must be an IANA zone, for example UTC or Europe/Moscow.")
        return
    defaults = default_periods(coverage, zone)
    st.subheader("Periods")
    left, right = st.columns(2)
    st.session_state.setdefault(keys.C_A_START, defaults[0])
    st.session_state.setdefault(keys.C_A_END, defaults[1])
    st.session_state.setdefault(keys.C_B_START, defaults[2])
    st.session_state.setdefault(keys.C_B_END, defaults[3])
    a_start = left.date_input("Period A start", key=keys.C_A_START)
    a_end = left.date_input("Period A end", key=keys.C_A_END)
    b_start = right.date_input("Period B start", key=keys.C_B_START)
    b_end = right.date_input("Period B end", key=keys.C_B_END)
    before_period = parse_period(a_start, a_end, zone_name, "Period A")
    after_period = parse_period(b_start, b_end, zone_name, "Period B")
    if before_period is None or after_period is None:
        return

    st.subheader("Filters")
    filter_columns = st.columns(3)
    source = filter_columns[0].text_input("Source", key=keys.C_SOURCE)
    ref = filter_columns[1].text_input("Ref (optional)", key=keys.C_REF)
    name = filter_columns[2].text_input("Pipeline name (optional)", key=keys.C_NAME)
    source_filter = source.strip() or None
    ref_filter = ref.strip() or None
    name_filter = name.strip() or None

    revision = runtime.storage.data_revision()
    before_snapshots = load_period(
        runtime.storage,
        revision,
        selected.instance_base_url,
        selected.project_id,
        before_period,
        source_filter,
        ref_filter,
        name_filter,
    )
    after_snapshots = load_period(
        runtime.storage,
        revision,
        selected.instance_base_url,
        selected.project_id,
        after_period,
        source_filter,
        ref_filter,
        name_filter,
    )
    all_stages = _stage_names(before_snapshots, after_snapshots)
    st.session_state[keys.ALL_STAGES] = all_stages

    st.subheader("Analysis")
    _sanitize_select(keys.C_LEVEL, list(LEVELS), "pipeline")
    level = st.radio("Level", LEVELS, format_func=str.title, horizontal=True, key=keys.C_LEVEL)
    stage = None
    job_stage = None
    job_name = None
    if level == "stage":
        stage_options = [""] + all_stages
        _sanitize_select(keys.C_STAGE, stage_options, "")
        stage_value = st.selectbox(
            "Stage",
            stage_options,
            format_func=lambda value: value or "(all stages)",
            key=keys.C_STAGE,
        )
        stage = stage_value or None
    if level == "job":
        stage_options = [""] + all_stages
        _sanitize_select(keys.C_JOB_STAGE, stage_options, "")
        job_stage_filter = st.selectbox(
            "Job stage filter",
            stage_options,
            format_func=lambda value: value or "(all stages)",
            key=keys.C_JOB_STAGE,
        )
        job_pairs = _job_options(job_stage_filter, before_snapshots, after_snapshots)
        job_options = [""] + [keys.job_option_key(stage_name, job) for stage_name, job in job_pairs]
        _sanitize_select(keys.C_JOB, job_options, "")
        job_value = st.selectbox(
            "Job",
            job_options,
            format_func=lambda value: (
                "(all jobs)" if not value else " :: ".join(keys.split_job_option(value))
            ),
            key=keys.C_JOB,
        )
        if job_value:
            job_stage, job_name = keys.split_job_option(job_value)
        else:
            job_stage, job_name = job_stage_filter or None, None

    enabled = st.checkbox(
        "Limit pipeline classification to selected stages",
        key=keys.C_SCOPE_ENABLED,
    )
    scope_stages: frozenset[str] | None = None
    if enabled:
        _sanitize_multiselect(keys.C_SCOPE_STAGES, all_stages)
        selected_scope = st.multiselect("Stages in scope", all_stages, key=keys.C_SCOPE_STAGES)
        st.caption("An empty selection means all stages.")
        if selected_scope and len(selected_scope) < len(all_stages):
            scope_stages = frozenset(selected_scope)

    st.subheader("Metrics")
    metric_left, metric_right = st.columns(2)
    _sanitize_select(keys.C_WALL_METRIC, list(WALL_METRICS), "mean")
    wall_metric = metric_left.selectbox(
        "Wall-clock metric",
        WALL_METRICS,
        format_func=lambda value: {"mean": "Mean", "median": "P50", "p95": "P95", "max": "Max"}[
            value
        ],
        key=keys.C_WALL_METRIC,
    )
    _sanitize_select(keys.C_PENDING_METRIC, list(PENDING_METRICS), "mean")
    pending_metric = metric_right.selectbox(
        "Pending metric",
        PENDING_METRICS,
        format_func=lambda value: {"mean": "Mean", "median": "P50", "p95": "P95"}[value],
        key=keys.C_PENDING_METRIC,
    )
    _sanitize_select(keys.C_DYNAMICS_METRIC, list(DYNAMICS_METRICS), "failure_rate")
    dynamics_metric = metric_left.selectbox(
        "Dynamics metric",
        DYNAMICS_METRICS,
        format_func=lambda value: _METRIC_LABELS[value],
        key=keys.C_DYNAMICS_METRIC,
    )
    _sanitize_select(keys.C_GRANULARITY, list(GRANULARITIES), "week")
    granularity = metric_right.selectbox(
        "Granularity",
        GRANULARITIES,
        format_func=lambda value: value.title(),
        key=keys.C_GRANULARITY,
    )
    st.session_state.setdefault(keys.C_INTEGRATION_PREFIX, "test integration")
    integration_prefix = st.text_input(
        "Integration job prefix",
        key=keys.C_INTEGRATION_PREFIX,
    )

    params = ComparisonParams(
        before=before_period,
        after=after_period,
        level=cast(AnalysisLevel, level),
        stage=stage,
        job_stage=job_stage,
        job_name=job_name,
        scope_stages=scope_stages,
        dynamics_metric=cast(DynamicsMetric, dynamics_metric),
        granularity=cast(Granularity, granularity),
        integration_prefix=integration_prefix,
    )
    report = build_report(before_snapshots, after_snapshots, params)

    if periods_overlap(a_start, a_end, b_start, b_end):
        st.warning("Periods A and B overlap; the samples are not independent.")
    incomplete = report.quality.before_incomplete + report.quality.after_incomplete
    if incomplete:
        st.warning(f"{incomplete} pipelines have incomplete data and are excluded where required.")
    stale = report.quality.before_stale + report.quality.after_stale
    if stale:
        st.warning(
            f"{stale} pipelines are stale: the last refresh did not complete, so the "
            "last complete snapshot is used."
        )
    if not before_snapshots and not after_snapshots:
        st.info("No stored pipelines match the selected periods and filters.")

    cards = st.columns(3)
    with cards[0]:
        _facts_column("Period A", report.before)
    with cards[1]:
        _facts_column("Period B", report.after)
    with cards[2]:
        _change_column(report)

    export = render_export_settings()

    st.subheader("Runs with failures")
    failure_text = render_chart_export("failure")
    failure_figure = annotate_note(
        failure_rate_chart(
            report.before.failure_rate,
            report.after.failure_rate,
            before_label="A",
            after_label="B",
            text=failure_text,
            export=export,
        ),
        _period_note(report),
    )
    render_figure(failure_figure, "failure", failure_text, export)

    st.subheader("Wall-clock")
    wall_text = render_chart_export("wall")
    wall_figure = annotate_note(
        duration_chart(
            report.before.wall,
            report.after.wall,
            metric=cast(WallMetric, wall_metric),
            before_label="A",
            after_label="B",
            text=wall_text,
            export=export,
        ),
        _period_note(report),
    )
    render_figure(wall_figure, "wall", wall_text, export)
    st.subheader("Wall-clock distribution")
    distribution_text = render_chart_export("distribution")
    distribution_figure = annotate_note(
        distribution_chart(
            report.before.wall_values,
            report.after.wall_values,
            before_label="A",
            after_label="B",
            text=distribution_text,
            export=export,
        ),
        _period_note(
            report,
            f"green n A={len(report.before.wall_values)} B={len(report.after.wall_values)}",
        ),
    )
    render_figure(distribution_figure, "distribution", distribution_text, export)

    st.subheader("Job queue wait")
    pending_text = render_chart_export("pending")
    pending_figure = annotate_note(
        pending_chart(
            report.before.pending,
            report.after.pending,
            metric=cast(PendingMetric, pending_metric),
            before_label="A",
            after_label="B",
            text=pending_text,
            export=export,
        ),
        _period_note(report),
    )
    render_figure(pending_figure, "pending", pending_text, export)

    st.subheader("Dynamics")
    metric_label = _METRIC_LABELS[dynamics_metric]
    if st.session_state.get(keys.DYNAMICS_TEXT_METRIC) != dynamics_metric:
        st.session_state[keys.chart_key("dynamics", "title")] = f"{metric_label} over time"
        st.session_state[keys.chart_key("dynamics", "y")] = metric_label
        st.session_state[keys.DYNAMICS_TEXT_METRIC] = dynamics_metric
    dynamics_text = render_chart_export(
        "dynamics",
        ChartText(
            title=f"{metric_label} over time",
            filename="glabviz-dynamics",
            x_label="Period bucket",
            y_label=metric_label,
        ),
    )
    dynamics_figure = annotate_note(
        dynamics_chart(
            report.dynamics,
            before_label="A",
            after_label="B",
            metric_label=metric_label,
            is_percent=dynamics_metric == "failure_rate",
            text=dynamics_text,
            export=export,
        ),
        _period_note(report, f"{metric_label}, by {granularity}"),
    )
    render_figure(dynamics_figure, "dynamics", dynamics_text, export)

    st.subheader("Stages and jobs")
    _render_table(report, level, all_stages)
    _render_quality(report, coverage, zone, level, scope_stages)
    _render_integration(report, integration_prefix)
    render_save_controls(runtime, "compare")
