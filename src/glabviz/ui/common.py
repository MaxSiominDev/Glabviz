from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from glabviz.analysis_config import ProjectRef
from glabviz.domain import MetricPeriod
from glabviz.storage import Coverage, StoredProject


def project_value(project: StoredProject) -> str:
    return f"{project.instance_base_url}|{project.project_id}"


def project_label(project: StoredProject) -> str:
    return project_label_parts(project.path, project.project_id, project.instance_base_url)


def project_label_parts(path: str, project_id: int, instance_base_url: str) -> str:
    return f"{path} (#{project_id} on {instance_base_url})"


def project_ref_from_value(value: str) -> ProjectRef | None:
    instance, _, project_id = value.partition("|")
    try:
        return ProjectRef(instance, int(project_id))
    except ValueError:
        return None


def pick_project(projects: list[StoredProject], key: str) -> StoredProject | None:
    if not projects:
        st.info("No data is stored yet. Load a project on the Data page, then come back here.")
        return None
    options = {project_value(project): project for project in projects}
    labels = {value: project_label(project) for value, project in options.items()}
    current = st.session_state.get(key)
    if current not in options:
        st.session_state[key] = next(iter(options))
    selected = st.selectbox(
        "Project", list(options), format_func=lambda value: labels[value], key=key
    )
    return options[selected]


def zone_or_none(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name.strip() or "UTC")
    except ZoneInfoNotFoundError, ValueError:
        return None


def parse_period(start: date, end: date, timezone_name: str, label: str) -> MetricPeriod | None:
    try:
        return MetricPeriod(start, end, timezone_name.strip() or "UTC")
    except ValueError as exc:
        st.error(f"{label}: {exc}")
        return None


def _month_end(value: date) -> date:
    if value.month == 12:
        following = date(value.year + 1, 1, 1)
    else:
        following = date(value.year, value.month + 1, 1)
    return following - timedelta(days=1)


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def default_periods(coverage: Coverage | None, zone: ZoneInfo) -> tuple[date, date, date, date]:
    if coverage is None or coverage.first_created_at is None or coverage.last_created_at is None:
        today = datetime.now(UTC).date()
        return (
            today - timedelta(days=60),
            today - timedelta(days=31),
            today - timedelta(days=30),
            today,
        )
    first = coverage.first_created_at.astimezone(zone).date()
    last = coverage.last_created_at.astimezone(zone).date()
    a_start = first
    a_end = min(_month_end(first), last)
    b_start = max(_month_start(last), first)
    b_end = last
    if b_start <= a_end:
        split = first + timedelta(days=max(0, (last - first).days // 2))
        a_start, a_end = first, split
        b_start, b_end = min(split + timedelta(days=1), last), last
        if b_start > b_end:
            b_start = b_end
    return a_start, a_end, b_start, b_end


def periods_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def format_local_date(value: datetime | None, zone: ZoneInfo) -> str:
    if value is None:
        return "No data"
    return value.astimezone(zone).strftime("%Y-%m-%d")


def coverage_caption(coverage: Coverage, zone: ZoneInfo) -> str:
    return (
        f"Stored history: {format_local_date(coverage.first_created_at, zone)}"
        f" .. {format_local_date(coverage.last_created_at, zone)}"
        f"; last successful sync: {format_local_date(coverage.last_successful_sync_at, zone)}"
    )
