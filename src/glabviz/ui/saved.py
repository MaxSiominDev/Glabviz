from __future__ import annotations

from zoneinfo import ZoneInfo

import streamlit as st

from glabviz.analysis_config import ConfigValidationError
from glabviz.formatting import format_datetime
from glabviz.runtime import Runtime
from glabviz.storage import SavedAnalysis, StorageError
from glabviz.ui import keys
from glabviz.ui.analysis_state import apply_config_now

_MESSAGE = "_saved_page_message"


def _label(record: SavedAnalysis) -> str:
    return f"{record.name} (#{record.project_id} on {record.instance_base_url})"


def _details(record: SavedAnalysis) -> None:
    config = record.config
    zone = ZoneInfo(config.before.timezone)
    st.write(f"**{record.name}**")
    st.caption(
        f"Updated {format_datetime(record.updated_at, zone)}; "
        f"project {record.project_id} on {record.instance_base_url}"
    )
    st.write(
        f"A: {config.before.start_date} .. {config.before.end_date} ({config.before.timezone}); "
        f"B: {config.after.start_date} .. {config.after.end_date} ({config.after.timezone})"
    )
    filters = config.filters
    st.write(
        "Filters: "
        f"source={filters.source or '(any)'}, ref={filters.ref or '(any)'}, "
        f"name={filters.name or '(any)'}"
    )
    comparison = config.comparison
    target = comparison.stage or comparison.job_name or "(pipeline)"
    st.write(f"Level: {comparison.level}; object: {target}")
    st.write(
        f"Dynamics: {comparison.dynamics_metric} by {comparison.granularity}; "
        f"integration prefix: {comparison.integration_prefix or '(disabled)'}"
    )
    gantt = config.gantt
    st.write(
        f"Gantt: A={gantt.primary_pipeline_id or '(none)'}, "
        f"B={gantt.secondary_pipeline_id or '(none)'}; "
        f"hidden stages={len(gantt.hidden_stages)}, hidden jobs={len(gantt.hidden_job_ids)}"
    )


def render_saved_page(runtime: Runtime) -> None:
    message = st.session_state.pop(_MESSAGE, None)
    if message:
        st.success(message)
    try:
        analyses = runtime.storage.analyses()
    except StorageError as exc:
        st.error(str(exc))
        return
    if not analyses:
        st.info("No saved comparisons yet. Configure one on the Compare page and save it there.")
        return
    by_id = {record.analysis_id: record for record in analyses}
    options = list(by_id)
    current = st.session_state.get(keys.SELECTED_ANALYSIS)
    if current not in by_id:
        current = options[0]
        st.session_state[keys.SELECTED_ANALYSIS] = current
    if st.session_state.get(keys.SAVED_SELECTION_MARKER) != current:
        st.session_state[keys.SAVED_RENAME_NAME] = by_id[current].name
        st.session_state[keys.SAVED_DELETE_CONFIRM] = False
        st.session_state[keys.SAVED_SELECTION_MARKER] = current

    selected = st.selectbox(
        "Saved comparison",
        options,
        format_func=lambda value: _label(by_id[value]),
        key=keys.SELECTED_ANALYSIS,
    )
    record = by_id[selected]
    _details(record)

    action, rename_column, delete_column = st.columns(3)
    if action.button("Open", key="saved_open"):
        stored = {
            (project.instance_base_url, project.project_id)
            for project in runtime.storage.projects()
        }
        project = record.config.project
        if (project.instance_base_url, project.project_id) not in stored:
            st.error("The project in this comparison is not stored in this data directory.")
        else:
            apply_config_now(record.config)
            st.session_state[keys.ACTIVE_ANALYSIS_ID] = record.analysis_id
            st.session_state[keys.SAVE_NAME] = record.name
            st.session_state[keys.NAV_TARGET] = "Compare"
            st.rerun()

    new_name = rename_column.text_input("New name", key=keys.SAVED_RENAME_NAME)
    if rename_column.button("Rename", key="saved_rename"):
        try:
            renamed = runtime.storage.rename_analysis(selected, new_name)
        except (StorageError, ConfigValidationError, ValueError) as exc:
            rename_column.error(str(exc))
        else:
            st.session_state[_MESSAGE] = f"Renamed to {renamed.name!r}."
            st.rerun()

    confirmed = delete_column.checkbox(
        "I understand deletion is permanent", key=keys.SAVED_DELETE_CONFIRM
    )
    if delete_column.button("Delete", key="saved_delete", disabled=not confirmed):
        try:
            runtime.storage.delete_analysis(selected)
        except StorageError as exc:
            delete_column.error(str(exc))
        else:
            if st.session_state.get(keys.ACTIVE_ANALYSIS_ID) == selected:
                st.session_state.pop(keys.ACTIVE_ANALYSIS_ID, None)
            st.session_state.pop(keys.SAVE_NAME, None)
            st.session_state[_MESSAGE] = f"Deleted {record.name!r}."
            st.rerun()
