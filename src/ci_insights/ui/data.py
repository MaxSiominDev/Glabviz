from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from ci_insights.collector import Collector, SyncRequest, SyncResult
from ci_insights.config import ca_bundle, gitlab_token, gitlab_url
from ci_insights.domain import MetricPeriod
from ci_insights.gitlab_client import (
    GitLabClient,
    GitLabError,
    GitLabSettings,
    canonical_instance_url,
)
from ci_insights.metrics import classify_pipeline
from ci_insights.runtime import Runtime, SyncAlreadyRunningError
from ci_insights.storage import Storage, StorageError, StoredProject

logger = logging.getLogger("ci_insights.ui")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo("UTC")


def _format_datetime(value: datetime | None, zone: ZoneInfo) -> str:
    if value is None:
        return "-"
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _settings(base_url: str, token: str, ca: str) -> GitLabSettings:
    if not base_url.strip():
        raise ValueError("GitLab base URL is required")
    if not token.strip():
        raise ValueError("GitLab token is required")
    return GitLabSettings(
        base_url=canonical_instance_url(base_url),
        token=token.strip(),
        ca_bundle=ca.strip() or None,
    )


def _run_sync(
    runtime: Runtime,
    *,
    base_url: str,
    project: str,
    token: str,
    source: str,
    ref: str,
    name: str,
    timezone_name: str,
    ca: str,
    start_date: date,
    end_date: date,
) -> SyncResult | None:
    try:
        if not project.strip():
            raise ValueError("GitLab project ID or path is required")
        settings = _settings(base_url, token, ca)
        period = MetricPeriod(start_date, end_date, timezone_name.strip() or "UTC")
        request = SyncRequest(
            instance_base_url=settings.base_url,
            project=project.strip(),
            period=period,
            source=source.strip() or None,
            ref=ref.strip() or None,
            name=name.strip() or None,
        )
        client = GitLabClient(settings)
        collector = Collector(runtime.storage, lambda: client)
        progress = st.progress(0.0, text="Starting sync")

        def on_progress(done: int, total: int, message: str) -> None:
            ratio = done / total if total else 0.0
            progress.progress(min(max(ratio, 0.0), 1.0), text=f"{message} ({done}/{total})")

        with runtime.sync_guard():
            result = collector.sync(request, on_progress)
        progress.empty()
        return result
    except SyncAlreadyRunningError as exc:
        st.warning(str(exc))
        return None
    except (ValueError, GitLabError, StorageError) as exc:
        st.error(str(exc))
        return None
    except Exception as exc:
        logger.exception("sync failed")
        st.error(f"Sync failed: {type(exc).__name__}: {exc}")
        return None


def _render_sync_result(result: SyncResult) -> None:
    summary = f"discovered {result.discovered}, stored {result.completed}; state: {result.state}"
    if result.state == "complete":
        st.success(summary)
    elif result.state == "partial":
        st.warning(summary)
    else:
        st.error(summary)
    if result.warnings:
        st.warning("\n".join(result.warnings))
    if result.errors:
        st.error("\n".join(result.errors))


def _render_stored_project(storage: Storage, selected: StoredProject) -> None:
    coverage = storage.coverage(selected.instance_base_url, selected.project_id)
    zone = _zone(selected.default_timezone)
    st.subheader(f"Stored data: {selected.path}")
    first, second, third, fourth = st.columns(4)
    first.metric("Pipelines", coverage.pipeline_count)
    second.metric("Incomplete", coverage.incomplete_pipelines)
    third.metric("First pipeline", _format_datetime(coverage.first_created_at, zone))
    fourth.metric("Last pipeline", _format_datetime(coverage.last_created_at, zone))
    st.caption(
        "Last successful sync: "
        + _format_datetime(coverage.last_successful_sync_at, zone)
        + "; last stored pipeline fetch: "
        + _format_datetime(coverage.last_fetched_at, zone)
    )
    runs = storage.sync_runs(selected.instance_base_url, selected.project_id, limit=10)
    if runs:
        st.write("Sync history")
        st.dataframe(
            [
                {
                    "started": _format_datetime(run.started_at, zone),
                    "finished": _format_datetime(run.finished_at, zone),
                    "state": run.state,
                    "requested": f"{run.requested_start_date}..{run.requested_end_date}",
                    "timezone": run.requested_timezone,
                    "discovered": run.discovered_count,
                    "completed": run.completed_count,
                    "errors": run.error_summary,
                }
                for run in runs
            ],
            hide_index=True,
            width="stretch",
        )
    snapshots = storage.recent_snapshots(selected.instance_base_url, selected.project_id, limit=50)
    if not snapshots:
        st.info("No pipelines are stored for this project.")
        return
    st.write("Recent pipelines")
    rows = []
    for snapshot in snapshots:
        category = classify_pipeline(snapshot, None)
        stale = (
            snapshot.source_discrepancy
            or snapshot.last_attempt_state not in (None, "complete")
            or bool(snapshot.last_error)
        )
        rows.append(
            {
                "pipeline": snapshot.pipeline_id,
                "created": _format_datetime(snapshot.created_at, zone),
                "source": snapshot.source,
                "gitlab_status": snapshot.source_status,
                "classification": category.value,
                "completeness": snapshot.completeness.value,
                "failed_attempts": sum(
                    1 for attempt in snapshot.attempts if attempt.counted_failure
                ),
                "allowed_failures": sum(
                    1
                    for attempt in snapshot.attempts
                    if attempt.observed_failure and attempt.allow_failure
                ),
                "stale": "yes" if stale else "",
                "last_error": snapshot.last_error,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")


def render_data_page(runtime: Runtime) -> None:
    projects = runtime.storage.projects()
    selected: StoredProject | None = None
    if projects:
        labels = [
            f"{project.path} (#{project.project_id} on {project.instance_base_url})"
            for project in projects
        ]
        label = st.selectbox("Stored project", labels, key="stored_project")
        selected = projects[labels.index(label)]
    default_url = selected.instance_base_url if selected else (gitlab_url() or "")
    default_project = selected.path if selected else ""
    default_filter = selected.local_filter if selected else {}
    if not isinstance(default_filter, dict):
        default_filter = {}
    if "source" in default_filter:
        default_source = default_filter.get("source") or ""
    else:
        default_source = "merge_request_event"
    default_ref = default_filter.get("ref") or ""
    default_name = default_filter.get("name") or ""
    default_timezone = selected.default_timezone if selected else "UTC"
    coverage = (
        runtime.storage.coverage(selected.instance_base_url, selected.project_id)
        if selected
        else None
    )
    if coverage and coverage.first_created_at and coverage.last_created_at:
        default_zone = _zone(default_timezone)
        default_start = coverage.first_created_at.astimezone(default_zone).date()
        default_end = coverage.last_created_at.astimezone(default_zone).date()
    else:
        default_end = datetime.now(UTC).date()
        default_start = default_end - timedelta(days=30)

    st.subheader("GitLab connection")
    with st.form("gitlab_sync"):
        base_url = st.text_input("GitLab base URL", value=default_url)
        project = st.text_input("Project ID or path", value=default_project)
        token = st.text_input(
            "Personal access token",
            value=gitlab_token() or "",
            type="password",
        )
        left, right = st.columns(2)
        source = left.text_input("Source filter", value=default_source)
        ref = right.text_input("Ref filter (optional)", value=default_ref)
        name = right.text_input("Pipeline name filter (optional)", value=default_name)
        bottom_left, bottom_right = st.columns(2)
        timezone_name = bottom_left.text_input("Timezone", value=default_timezone)
        ca = bottom_left.text_input("CA bundle (optional)", value=ca_bundle() or "")
        start_date = bottom_right.date_input("Start date", value=default_start)
        end_date = bottom_right.date_input("End date", value=default_end)
        test_clicked = st.form_submit_button("Test connection")
        load_clicked = st.form_submit_button("Load / update")

    if test_clicked:
        try:
            if not project.strip():
                raise ValueError("GitLab project ID or path is required")
            settings = _settings(base_url, token, ca)
            info = GitLabClient(settings).check_project(project.strip())
            st.success(f"Connected to {info.path} (id {info.project_id})")
        except (ValueError, GitLabError) as exc:
            st.error(str(exc))

    if load_clicked:
        st.session_state.pop("last_sync_result", None)
        result = _run_sync(
            runtime,
            base_url=base_url,
            project=project,
            token=token,
            source=source,
            ref=ref,
            name=name,
            timezone_name=timezone_name,
            ca=ca,
            start_date=start_date,
            end_date=end_date,
        )
        if result is not None:
            st.session_state["last_sync_result"] = result
            st.session_state["stored_project"] = (
                f"{result.project.path} (#{result.project.project_id} "
                f"on {result.instance_base_url})"
            )
            st.rerun()

    result = st.session_state.get("last_sync_result")
    if isinstance(result, SyncResult) and (
        selected is None
        or (
            selected.instance_base_url == result.instance_base_url
            and selected.project_id == result.project.project_id
        )
    ):
        _render_sync_result(result)

    if selected is None:
        st.info("No data is stored yet. Fill in the connection form and run Load / update.")
        return
    _render_stored_project(runtime.storage, selected)
