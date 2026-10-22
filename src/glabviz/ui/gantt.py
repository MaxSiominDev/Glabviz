from __future__ import annotations

from collections import defaultdict
from typing import cast
from zoneinfo import ZoneInfo

import streamlit as st

from glabviz.analysis import PipelineCatalogRow, build_pipeline_catalog
from glabviz.analysis_config import (
    GANTT_SORTS,
    SORT_SCOPES,
    ChartText,
    GanttSort,
    SortScope,
)
from glabviz.domain import PipelineSnapshot
from glabviz.formatting import format_datetime, format_seconds
from glabviz.gantt import (
    GanttOptions,
    GanttPanel,
    build_gantt_figure,
    build_gantt_panel,
    gantt_height,
    row_table,
)
from glabviz.runtime import Runtime
from glabviz.storage import StoredProject
from glabviz.ui import keys
from glabviz.ui.analysis_state import clear_saved_selection, render_save_controls
from glabviz.ui.common import pick_project, project_value, zone_or_none
from glabviz.ui.export import (
    render_chart_export,
    render_export_settings,
    render_figure,
)
from glabviz.ui.loaders import load_all


def _catalog_label(row: PipelineCatalogRow, zone: ZoneInfo) -> str:
    return (
        f"#{row.pipeline_id} · {format_datetime(row.created_at, zone)} · "
        f"{row.category.value} · {row.source_status}"
    )


def _sanitize_select(key: str, options: list, default: object) -> None:
    if st.session_state.get(key) not in options:
        st.session_state.pop(key, None)
        st.session_state[key] = default


def _visible_values(key: str, options: list[str]) -> list[str]:
    current = st.session_state.get(key)
    if not isinstance(current, list):
        st.session_state[key] = list(options)
        return list(options)
    filtered = [value for value in current if value in options]
    if current and not filtered:
        filtered = list(options)
    st.session_state[key] = filtered
    return filtered


def _show_all(key: str, options: list[str]) -> None:
    st.session_state[key] = list(options)


def _hide_selected(key: str, options: list[str]) -> None:
    current = st.session_state.get(key, [])
    st.session_state[key] = [value for value in options if value not in current]


def _reset_range() -> None:
    st.session_state[keys.G_RANGE_START] = None
    st.session_state[keys.G_RANGE_END] = None


def _reset_for_project(selected: StoredProject, *, project_changed: bool) -> None:
    value = project_value(selected)
    for key in (
        keys.G_PIPELINE_A,
        keys.G_PIPELINE_B,
        keys.G_VISIBLE_STAGES,
        keys.G_VISIBLE_JOBS,
        keys.GANTT_STATE,
        keys.GANTT_PENDING_HIDDEN,
    ):
        st.session_state.pop(key, None)
    st.session_state[keys.G_SORT_BY] = "start"
    st.session_state[keys.G_SORT_SCOPE] = "stage"
    st.session_state[keys.G_DESCENDING] = False
    st.session_state[keys.G_RANGE_START] = None
    st.session_state[keys.G_RANGE_END] = None
    st.session_state[keys.GANTT_PREVIOUS_PROJECT] = value
    if project_changed or keys.C_TIMEZONE not in st.session_state:
        st.session_state[keys.C_TIMEZONE] = selected.default_timezone or "UTC"
    if project_changed:
        clear_saved_selection()
        for key in (
            keys.C_SOURCE,
            keys.C_REF,
            keys.C_NAME,
            keys.C_A_START,
            keys.C_A_END,
            keys.C_B_START,
            keys.C_B_END,
            keys.C_LEVEL,
            keys.C_STAGE,
            keys.C_JOB_STAGE,
            keys.C_JOB,
            keys.C_SCOPE_ENABLED,
            keys.C_SCOPE_STAGES,
            keys.COMPARE_PREVIOUS_PROJECT,
        ):
            st.session_state.pop(key, None)


def _has_drawable(snapshot: PipelineSnapshot) -> bool:
    return any(
        attempt.started_at is not None
        and attempt.finished_at is not None
        and attempt.finished_at >= attempt.started_at
        for attempt in snapshot.attempts
    )


def _pipeline_rows(
    catalog: dict[int, PipelineCatalogRow], ids: list[int], zone: ZoneInfo
) -> list[dict[str, object]]:
    return [
        {
            "id": row.pipeline_id,
            "created": format_datetime(row.created_at, zone),
            "source status": row.source_status,
            "classification": row.category.value,
            "wall": format_seconds(row.wall_s),
            "completeness": row.completeness.value,
        }
        for row in (catalog[pipeline_id] for pipeline_id in ids)
    ]


def _job_id_map(panels: list[GanttPanel]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for panel in panels:
        for row in panel.drawable:
            result[keys.job_option_key(row.stage, row.exact_name)].add(row.job_id)
    return dict(result)


def _apply_pending_hidden(job_ids: dict[str, set[int]], all_stages: list[str]) -> None:
    pending = st.session_state.pop(keys.GANTT_PENDING_HIDDEN, None)
    if not isinstance(pending, tuple) or len(pending) != 2:
        return
    hidden_stages, hidden_job_ids = pending
    hidden_stages = set(hidden_stages)
    hidden_job_ids = set(hidden_job_ids)
    st.session_state[keys.G_VISIBLE_STAGES] = [
        stage for stage in all_stages if stage not in hidden_stages
    ]
    st.session_state[keys.G_VISIBLE_JOBS] = [
        key for key, ids in job_ids.items() if not (ids & hidden_job_ids)
    ]


def render_gantt_page(runtime: Runtime) -> None:
    projects = runtime.storage.projects()
    previous_project = st.session_state.get(keys.C_PROJECT)
    selected = pick_project(projects, keys.C_PROJECT)
    if selected is None:
        return
    current_project = project_value(selected)
    if st.session_state.get(keys.GANTT_PREVIOUS_PROJECT) != current_project:
        _reset_for_project(selected, project_changed=previous_project != current_project)

    if keys.C_TIMEZONE not in st.session_state:
        st.session_state[keys.C_TIMEZONE] = selected.default_timezone or "UTC"
    zone_name = st.text_input("Timezone", key=keys.C_TIMEZONE)
    zone = zone_or_none(zone_name)
    if zone is None:
        st.error("Timezone must be an IANA zone, for example UTC or Europe/Moscow.")
        return

    revision = runtime.storage.data_revision()
    snapshots = load_all(runtime.storage, revision, selected.instance_base_url, selected.project_id)
    if not snapshots:
        st.info("No pipelines are stored for this project.")
        return
    catalog = {row.pipeline_id: row for row in build_pipeline_catalog(snapshots)}
    by_id = {snapshot.pipeline_id: snapshot for snapshot in snapshots}
    all_ids = list(catalog)

    search = st.text_input("Find pipeline by ID", key=keys.G_SEARCH)
    if search.strip():
        ids = [pipeline_id for pipeline_id in all_ids if search.strip() in str(pipeline_id)]
    else:
        ids = all_ids[:200]
    selected_a = st.session_state.get(keys.G_PIPELINE_A)
    if isinstance(selected_a, int) and selected_a not in catalog:
        st.warning(f"Saved pipeline {selected_a} is not stored in this data directory.")
    if isinstance(selected_a, int) and selected_a in catalog and selected_a not in ids:
        ids = [selected_a, *ids]
    selected_b = st.session_state.get(keys.G_PIPELINE_B)
    if isinstance(selected_b, int) and selected_b not in catalog:
        st.warning(f"Saved pipeline {selected_b} is not stored in this data directory.")
    if isinstance(selected_b, int) and selected_b in catalog and selected_b not in ids:
        ids = [selected_b, *ids]
    if not ids:
        st.info("No stored pipeline matches this ID.")
        return

    st.dataframe(
        _pipeline_rows(catalog, ids, zone),
        hide_index=True,
        width="stretch",
    )
    st.caption("Showing the newest 200 pipelines unless a search narrows the list.")

    default_pipeline = next((value for value in ids if _has_drawable(by_id[value])), ids[0])
    _sanitize_select(keys.G_PIPELINE_A, ids, default_pipeline)
    pipeline_a = st.selectbox(
        "Pipeline A",
        ids,
        format_func=lambda value: _catalog_label(catalog[value], zone),
        key=keys.G_PIPELINE_A,
    )
    pipeline_options = [None, *ids]
    _sanitize_select(keys.G_PIPELINE_B, pipeline_options, None)
    pipeline_b = st.selectbox(
        "Pipeline B (optional)",
        pipeline_options,
        format_func=lambda value: (
            "(none)" if value is None else _catalog_label(catalog[value], zone)
        ),
        key=keys.G_PIPELINE_B,
    )

    snapshot_a = by_id[pipeline_a]
    snapshot_b = by_id[pipeline_b] if pipeline_b is not None else None
    preliminary = [build_gantt_panel(snapshot_a, GanttOptions())]
    if snapshot_b is not None:
        preliminary.append(build_gantt_panel(snapshot_b, GanttOptions()))
    job_ids = _job_id_map(preliminary)
    all_stages = sorted({row.stage for panel in preliminary for row in panel.drawable})
    all_jobs = sorted(job_ids)
    _apply_pending_hidden(job_ids, all_stages)
    _visible_values(keys.G_VISIBLE_STAGES, all_stages)
    _visible_values(keys.G_VISIBLE_JOBS, all_jobs)

    st.subheader("Rows")
    stage_column, job_column = st.columns(2)
    with stage_column:
        chosen_stages = st.multiselect("Visible stages", all_stages, key=keys.G_VISIBLE_STAGES)
        first, second = st.columns(2)
        first.button(
            "Show all",
            key="show_all_stages",
            on_click=_show_all,
            args=(keys.G_VISIBLE_STAGES, all_stages),
        )
        second.button(
            "Hide selected",
            key="hide_selected_stages",
            on_click=_hide_selected,
            args=(keys.G_VISIBLE_STAGES, all_stages),
        )
    with job_column:
        chosen_jobs = st.multiselect(
            "Visible jobs",
            all_jobs,
            format_func=lambda value: " :: ".join(keys.split_job_option(value)),
            key=keys.G_VISIBLE_JOBS,
        )
        first, second = st.columns(2)
        first.button(
            "Show all",
            key="show_all_jobs",
            on_click=_show_all,
            args=(keys.G_VISIBLE_JOBS, all_jobs),
        )
        second.button(
            "Hide selected",
            key="hide_selected_jobs",
            on_click=_hide_selected,
            args=(keys.G_VISIBLE_JOBS, all_jobs),
        )
    job_search = st.text_input("Find job", key=keys.G_JOB_SEARCH)

    st.subheader("Layout")
    layout_left, layout_right = st.columns(2)
    _sanitize_select(keys.G_SORT_BY, list(GANTT_SORTS), "start")
    sort_by = layout_left.selectbox(
        "Sort by",
        GANTT_SORTS,
        format_func=lambda value: value.title(),
        key=keys.G_SORT_BY,
    )
    _sanitize_select(keys.G_SORT_SCOPE, list(SORT_SCOPES), "stage")
    sort_scope = layout_right.radio(
        "Sort scope",
        SORT_SCOPES,
        format_func=lambda value: value.title(),
        horizontal=True,
        key=keys.G_SORT_SCOPE,
    )
    descending = layout_right.checkbox("Descending", key=keys.G_DESCENDING)
    range_left, range_right, range_reset = st.columns(3)
    st.session_state.setdefault(keys.G_RANGE_START, None)
    st.session_state.setdefault(keys.G_RANGE_END, None)
    x_min: float | None = range_left.number_input(
        "Range start (seconds)",
        step=60.0,
        format="%.0f",
        key=keys.G_RANGE_START,
    )
    x_max: float | None = range_right.number_input(
        "Range end (seconds)",
        step=60.0,
        format="%.0f",
        key=keys.G_RANGE_END,
    )
    range_reset.button("Reset range", key="reset_gantt_range", on_click=_reset_range)
    if (x_min is None) != (x_max is None):
        st.info("Set both range bounds or clear both to use the automatic range.")
        x_min, x_max = None, None
    elif x_min is not None and x_max is not None and x_min >= x_max:
        st.error("Range start must be less than range end.")
        x_min, x_max = None, None

    hidden_stages = frozenset(set(all_stages) - set(chosen_stages))
    hidden_job_ids = frozenset(
        job_id
        for key, ids_for_job in job_ids.items()
        if key not in chosen_jobs
        for job_id in ids_for_job
    )
    options = GanttOptions(
        sort_by=cast(GanttSort, sort_by),
        descending=descending,
        sort_scope=cast(SortScope, sort_scope),
        hidden_stages=hidden_stages,
        hidden_job_ids=hidden_job_ids,
        search=job_search,
        x_min_s=float(x_min) if x_min is not None else None,
        x_max_s=float(x_max) if x_max is not None else None,
    )
    panels = [build_gantt_panel(snapshot_a, options)]
    if snapshot_b is not None:
        panels.append(build_gantt_panel(snapshot_b, options))
    st.session_state[keys.GANTT_STATE] = {
        "project": current_project,
        "primary": pipeline_a,
        "secondary": pipeline_b,
        "hidden_stages": sorted(hidden_stages),
        "hidden_job_ids": sorted(hidden_job_ids),
        "sort_by": sort_by,
        "sort_scope": sort_scope,
        "descending": descending,
        "x_min_s": float(x_min) if x_min is not None else None,
        "x_max_s": float(x_max) if x_max is not None else None,
    }
    if not any(panel.rows for panel in panels):
        st.info("No attempts are visible with the current filters.")
        unplotted = [
            row for panel in panels for row in row_table([panel], zone) if not row["plotted"]
        ]
        if unplotted:
            with st.expander("Attempts not plotted", expanded=True):
                st.caption(
                    "Rows without a valid start and finish pair are listed, not drawn as zero."
                )
                st.dataframe(unplotted, hide_index=True, width="stretch")
        render_save_controls(runtime, "gantt")
        return

    export = render_export_settings()
    height = gantt_height(panels, options)
    gantt_text = render_chart_export(
        "gantt",
        ChartText(
            title="",
            filename="glabviz-gantt",
            x_label="Elapsed time from pipeline start",
            y_label="Attempt",
        ),
    )
    figure = build_gantt_figure(
        panels, options, text=gantt_text, export=export, zone=zone, height=height
    )
    render_figure(
        figure,
        "gantt",
        gantt_text,
        export,
        height=max(export.height, height),
    )

    table = row_table(panels, zone)
    plotted = [
        {key: value for key, value in row.items() if key != "plotted"}
        for row in table
        if row["plotted"]
    ]
    unplotted = [row for row in table if not row["plotted"]]
    st.subheader("Attempts")
    st.dataframe(
        plotted,
        hide_index=True,
        width="stretch",
        column_config={
            "url": st.column_config.LinkColumn("Job"),
            "wall_s": st.column_config.NumberColumn(format="%.0f"),
            "queued_s": st.column_config.NumberColumn(format="%.0f"),
            "gitlab_s": st.column_config.NumberColumn(format="%.0f"),
            "finish_offset_s": st.column_config.NumberColumn(format="%.0f"),
        },
    )
    if unplotted:
        with st.expander("Attempts not plotted", expanded=False):
            st.caption("Rows without a valid start and finish pair are listed, not drawn as zero.")
            st.dataframe(unplotted, hide_index=True, width="stretch")
    render_save_controls(runtime, "gantt")
