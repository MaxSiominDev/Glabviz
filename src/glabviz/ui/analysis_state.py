from __future__ import annotations

from datetime import date

import streamlit as st

from glabviz.analysis_config import (
    ComparisonConfig,
    ConfigValidationError,
    ExportConfig,
    GanttConfig,
    PeriodConfig,
    PipelineFilters,
    ProjectRef,
    SavedAnalysisConfig,
)
from glabviz.runtime import Runtime
from glabviz.storage import SavedAnalysis, StorageError
from glabviz.ui import keys
from glabviz.ui.common import (
    default_periods,
    parse_period,
    project_ref_from_value,
    zone_or_none,
)
from glabviz.ui.export import collect_chart_texts, default_text

_SAVE_MESSAGE = "_save_message"


def _set_export(config: SavedAnalysisConfig) -> None:
    export = config.export
    st.session_state[keys.E_FORMAT] = export.image_format
    st.session_state[keys.E_WIDTH] = export.width
    st.session_state[keys.E_HEIGHT] = export.height
    st.session_state[keys.E_FONT_SIZE] = export.font_size
    st.session_state[keys.E_SCALE] = export.scale
    if "dynamics" in export.chart_texts:
        st.session_state[keys.DYNAMICS_TEXT_METRIC] = config.comparison.dynamics_metric
    else:
        st.session_state.pop(keys.DYNAMICS_TEXT_METRIC, None)
    for chart_id in keys.CHART_IDS:
        text = export.chart_texts.get(chart_id, default_text(chart_id))
        st.session_state[keys.chart_key(chart_id, "title")] = text.title
        st.session_state[keys.chart_key(chart_id, "filename")] = text.filename
        st.session_state[keys.chart_key(chart_id, "x")] = text.x_label
        st.session_state[keys.chart_key(chart_id, "y")] = text.y_label


def _set_compare(config: SavedAnalysisConfig) -> None:
    comparison = config.comparison
    value = f"{config.project.instance_base_url}|{config.project.project_id}"
    st.session_state[keys.C_PROJECT] = value
    st.session_state[keys.COMPARE_PREVIOUS_PROJECT] = value
    st.session_state[keys.C_SOURCE] = config.filters.source or ""
    st.session_state[keys.C_REF] = config.filters.ref or ""
    st.session_state[keys.C_NAME] = config.filters.name or ""
    st.session_state[keys.C_TIMEZONE] = config.before.timezone
    st.session_state[keys.C_A_START] = config.before.start_date
    st.session_state[keys.C_A_END] = config.before.end_date
    st.session_state[keys.C_B_START] = config.after.start_date
    st.session_state[keys.C_B_END] = config.after.end_date
    st.session_state[keys.C_LEVEL] = comparison.level
    st.session_state[keys.C_STAGE] = comparison.stage or ""
    st.session_state[keys.C_JOB_STAGE] = comparison.job_stage or ""
    st.session_state[keys.C_JOB] = (
        keys.job_option_key(comparison.job_stage, comparison.job_name)
        if comparison.job_stage and comparison.job_name
        else ""
    )
    st.session_state[keys.C_SCOPE_ENABLED] = bool(comparison.scope_stages)
    st.session_state[keys.C_SCOPE_STAGES] = list(comparison.scope_stages)
    st.session_state[keys.C_WALL_METRIC] = comparison.wall_metric
    st.session_state[keys.C_PENDING_METRIC] = comparison.pending_metric
    st.session_state[keys.C_DYNAMICS_METRIC] = comparison.dynamics_metric
    st.session_state[keys.C_GRANULARITY] = comparison.granularity
    st.session_state[keys.C_INTEGRATION_PREFIX] = comparison.integration_prefix


def _set_gantt(config: SavedAnalysisConfig) -> None:
    gantt = config.gantt
    value = f"{config.project.instance_base_url}|{config.project.project_id}"
    st.session_state[keys.C_PROJECT] = value
    st.session_state[keys.C_TIMEZONE] = config.before.timezone
    st.session_state[keys.GANTT_PREVIOUS_PROJECT] = value
    st.session_state[keys.G_PIPELINE_A] = gantt.primary_pipeline_id
    st.session_state[keys.G_PIPELINE_B] = gantt.secondary_pipeline_id
    st.session_state[keys.G_SORT_BY] = gantt.sort_by
    st.session_state[keys.G_SORT_SCOPE] = gantt.sort_scope
    st.session_state[keys.G_DESCENDING] = gantt.sort_descending
    st.session_state[keys.G_RANGE_START] = gantt.x_min_s
    st.session_state[keys.G_RANGE_END] = gantt.x_max_s
    st.session_state[keys.GANTT_PENDING_HIDDEN] = (
        list(gantt.hidden_stages),
        list(gantt.hidden_job_ids),
    )
    st.session_state[keys.GANTT_STATE] = {
        "project": value,
        "primary": gantt.primary_pipeline_id,
        "secondary": gantt.secondary_pipeline_id,
        "hidden_stages": list(gantt.hidden_stages),
        "hidden_job_ids": list(gantt.hidden_job_ids),
        "sort_by": gantt.sort_by,
        "sort_scope": gantt.sort_scope,
        "descending": gantt.sort_descending,
        "x_min_s": gantt.x_min_s,
        "x_max_s": gantt.x_max_s,
    }


def apply_config_now(config: SavedAnalysisConfig) -> None:
    _set_compare(config)
    _set_gantt(config)
    _set_export(config)


def job_selection(job_stage_value: str, job_value: str) -> tuple[str | None, str | None]:
    if job_value:
        return keys.split_job_option(job_value)
    return job_stage_value or None, None


def _date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _period(start: object, end: object, timezone_name: str) -> PeriodConfig | None:
    start_date = _date(start)
    end_date = _date(end)
    if start_date is None or end_date is None:
        return None
    period = parse_period(start_date, end_date, timezone_name, "Saved period")
    if period is None:
        return None
    return PeriodConfig(period.start_date, period.end_date, period.timezone)


def _compare_defaults(
    runtime: Runtime, project: ProjectRef, timezone_name: str
) -> tuple[PeriodConfig, PeriodConfig] | None:
    zone = zone_or_none(timezone_name)
    if zone is None:
        return None
    coverage = runtime.storage.coverage(project.instance_base_url, project.project_id)
    dates = default_periods(coverage, zone)
    return (
        PeriodConfig(dates[0], dates[1], timezone_name),
        PeriodConfig(dates[2], dates[3], timezone_name),
    )


def current_config(runtime: Runtime) -> SavedAnalysisConfig | None:
    project_key = keys.C_PROJECT
    project_value = st.session_state.get(project_key)
    if not isinstance(project_value, str):
        st.warning("Select a project before saving a comparison.")
        return None
    project = project_ref_from_value(project_value)
    if project is None:
        st.warning("The selected project is not valid.")
        return None
    timezone_name = str(st.session_state.get(keys.C_TIMEZONE, "UTC")).strip() or "UTC"
    if zone_or_none(timezone_name) is None:
        st.warning("The timezone is not a known IANA zone.")
        return None
    before = _period(
        st.session_state.get(keys.C_A_START),
        st.session_state.get(keys.C_A_END),
        timezone_name,
    )
    after = _period(
        st.session_state.get(keys.C_B_START),
        st.session_state.get(keys.C_B_END),
        timezone_name,
    )
    if before is None or after is None:
        defaults = _compare_defaults(runtime, project, timezone_name)
        if defaults is None:
            st.warning("Set both periods before saving a comparison.")
            return None
        before = before or defaults[0]
        after = after or defaults[1]
    level = str(st.session_state.get(keys.C_LEVEL, "pipeline"))
    stage = str(st.session_state.get(keys.C_STAGE, "")) or None
    job_stage, job_name = job_selection(
        str(st.session_state.get(keys.C_JOB_STAGE, "")),
        str(st.session_state.get(keys.C_JOB, "")),
    )
    if level == "stage" and stage is None:
        st.warning("Select a stage before saving a stage-level comparison.")
        return None
    if level == "job" and (job_stage is None or job_name is None):
        st.warning("Select a job before saving a job-level comparison.")
        return None
    all_stages = st.session_state.get(keys.ALL_STAGES, [])
    selected_scope = st.session_state.get(keys.C_SCOPE_STAGES, [])
    scope: tuple[str, ...] = ()
    if (
        st.session_state.get(keys.C_SCOPE_ENABLED, False)
        and selected_scope
        and set(all_stages) - set(selected_scope)
    ):
        scope = tuple(sorted(selected_scope))
    gantt = st.session_state.get(keys.GANTT_STATE, {})
    expected_project = f"{project.instance_base_url}|{project.project_id}"
    if not isinstance(gantt, dict) or gantt.get("project") != expected_project:
        gantt = {}
    gantt_config = GanttConfig(
        primary_pipeline_id=gantt.get("primary"),
        secondary_pipeline_id=gantt.get("secondary"),
        hidden_stages=tuple(gantt.get("hidden_stages", [])),
        hidden_job_ids=tuple(gantt.get("hidden_job_ids", [])),
        sort_by=gantt.get("sort_by", "start"),
        sort_descending=bool(gantt.get("descending", False)),
        sort_scope=gantt.get("sort_scope", "stage"),
        x_min_s=gantt.get("x_min_s"),
        x_max_s=gantt.get("x_max_s"),
    )
    return SavedAnalysisConfig(
        project=project,
        filters=PipelineFilters(
            source=str(st.session_state.get(keys.C_SOURCE, "")).strip() or None,
            ref=str(st.session_state.get(keys.C_REF, "")).strip() or None,
            name=str(st.session_state.get(keys.C_NAME, "")).strip() or None,
        ),
        before=before,
        after=after,
        comparison=ComparisonConfig(
            level=level,  # type: ignore[arg-type]
            stage=stage,
            job_stage=job_stage,
            job_name=job_name,
            scope_stages=scope,
            wall_metric=str(st.session_state.get(keys.C_WALL_METRIC, "mean")),  # type: ignore[arg-type]
            pending_metric=str(st.session_state.get(keys.C_PENDING_METRIC, "mean")),  # type: ignore[arg-type]
            dynamics_metric=str(st.session_state.get(keys.C_DYNAMICS_METRIC, "failure_rate")),  # type: ignore[arg-type]
            granularity=str(st.session_state.get(keys.C_GRANULARITY, "week")),  # type: ignore[arg-type]
            integration_prefix=str(
                st.session_state.get(keys.C_INTEGRATION_PREFIX, "test integration")
            ),
        ),
        export=ExportConfig(
            image_format=str(st.session_state.get(keys.E_FORMAT, "png")),  # type: ignore[arg-type]
            width=int(st.session_state.get(keys.E_WIDTH, 1200)),
            height=int(st.session_state.get(keys.E_HEIGHT, 700)),
            font_size=int(st.session_state.get(keys.E_FONT_SIZE, 12)),
            scale=float(st.session_state.get(keys.E_SCALE, 2.0)),
            chart_texts=collect_chart_texts(),
        ),
        gantt=gantt_config,
    )


def _analysis_label(record: SavedAnalysis) -> str:
    return f"{record.name} (#{record.project_id} on {record.instance_base_url})"


def clear_saved_selection() -> None:
    for key in (
        keys.ACTIVE_ANALYSIS_ID,
        keys.SELECTED_ANALYSIS,
        keys.SELECTED_PENDING,
        keys.SAVE_NAME,
    ):
        st.session_state.pop(key, None)


def render_save_controls(runtime: Runtime, page: str) -> None:
    message = st.session_state.pop(_SAVE_MESSAGE, None)
    if message:
        st.success(message)
    with st.expander("Saved comparison", expanded=False):
        try:
            analyses = runtime.storage.analyses()
        except StorageError as exc:
            st.error(str(exc))
            return
        project_value = st.session_state.get(keys.C_PROJECT)
        if isinstance(project_value, str):
            analyses = [
                record
                for record in analyses
                if f"{record.instance_base_url}|{record.project_id}" == project_value
            ]
        by_id = {record.analysis_id: record for record in analyses}
        options = list(by_id)
        pending = st.session_state.pop(keys.SELECTED_PENDING, None)
        if isinstance(pending, str) and pending in by_id:
            st.session_state[keys.SELECTED_ANALYSIS] = pending
        if options:
            previous = st.session_state.get(keys.SELECTED_ANALYSIS)
            preferred = previous
            if preferred not in by_id:
                preferred = st.session_state.get(keys.ACTIVE_ANALYSIS_ID)
            if preferred not in by_id:
                preferred = options[0]
            st.session_state[keys.SELECTED_ANALYSIS] = preferred
            if preferred != previous:
                st.session_state[keys.SAVE_NAME] = by_id[preferred].name

            def _sync_name() -> None:
                selected_id = st.session_state.get(keys.SELECTED_ANALYSIS)
                record = by_id.get(selected_id) if isinstance(selected_id, str) else None
                if record is not None:
                    st.session_state[keys.SAVE_NAME] = record.name

            selected = st.selectbox(
                "Existing comparison",
                options,
                format_func=lambda value: _analysis_label(by_id[value]),
                key=keys.SELECTED_ANALYSIS,
                on_change=_sync_name,
            )
        else:
            selected = None
            st.caption("No saved comparisons yet for this project.")
        if keys.SAVE_NAME not in st.session_state:
            st.session_state[keys.SAVE_NAME] = by_id[selected].name if selected is not None else ""
        name = st.text_input("Name", key=keys.SAVE_NAME)
        left, right = st.columns(2)
        if left.button("Save as new", key=f"save_new_{page}"):
            config = current_config(runtime)
            if config is None:
                st.error("The current comparison cannot be saved yet.")
            else:
                try:
                    created = runtime.storage.save_analysis(name, config)
                except (StorageError, ConfigValidationError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state[keys.ACTIVE_ANALYSIS_ID] = created.analysis_id
                    st.session_state[keys.SELECTED_PENDING] = created.analysis_id
                    st.session_state[_SAVE_MESSAGE] = f"Saved {created.name!r}."
                    st.rerun()
        if right.button(
            "Save changes",
            key=f"save_update_{page}",
            disabled=selected is None,
        ):
            config = current_config(runtime)
            if config is None:
                st.error("The current comparison cannot be saved yet.")
            elif selected is not None:
                try:
                    updated = runtime.storage.update_analysis(
                        selected, name=name or by_id[selected].name, config=config
                    )
                except (StorageError, ConfigValidationError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state[keys.ACTIVE_ANALYSIS_ID] = updated.analysis_id
                    st.session_state[keys.SELECTED_PENDING] = updated.analysis_id
                    st.session_state[_SAVE_MESSAGE] = f"Updated {updated.name!r}."
                    st.rerun()
