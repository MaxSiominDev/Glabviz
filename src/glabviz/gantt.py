from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from glabviz.analysis_config import (
    ChartText,
    ExportConfig,
    GanttSort,
    SortScope,
)
from glabviz.charts import (
    AXIS,
    CRITICAL,
    GOOD,
    GRID,
    INK,
    INK_SECONDARY,
    MUTED,
    SERIES_BEFORE,
    SURFACE,
    style_figure,
)
from glabviz.domain import AttemptKind, JobAttempt, PipelineSnapshot
from glabviz.formatting import (
    escape_text,
    format_datetime,
    format_seconds,
    nice_ticks,
)

CANCELED = "#898781"
SKIPPED = "#c3c2b7"
WARNING = "#fab219"
SERIOUS = "#ec835a"

_STATUS_COLORS = {
    "success": GOOD,
    "failed": CRITICAL,
    "failed (allowed)": CRITICAL,
    "canceled": CANCELED,
    "skipped": SKIPPED,
    "running": SERIES_BEFORE,
    "pending": SERIES_BEFORE,
    "preparing": SERIES_BEFORE,
    "scheduled": SERIES_BEFORE,
    "created": WARNING,
    "manual": WARNING,
    "waiting_for_resource": WARNING,
}

_LEGEND_ORDER = (
    "success",
    "failed",
    "failed (allowed)",
    "canceled",
    "skipped",
    "running",
    "pending",
    "preparing",
    "scheduled",
    "created",
    "manual",
    "waiting_for_resource",
    "other",
)


@dataclass(frozen=True)
class GanttOptions:
    sort_by: GanttSort = "start"
    descending: bool = False
    sort_scope: SortScope = "stage"
    hidden_stages: frozenset[str] = frozenset()
    hidden_job_ids: frozenset[int] = frozenset()
    search: str = ""
    x_min_s: float | None = None
    x_max_s: float | None = None


@dataclass(frozen=True)
class GanttRow:
    pipeline_id: int
    job_id: int
    exact_name: str
    stage: str
    kind: AttemptKind
    status: str
    allow_failure: bool
    is_current_attempt: bool | None
    started_at: datetime | None
    finished_at: datetime | None
    wall_s: float | None
    gitlab_duration_s: float | None
    queued_duration_s: float | None
    web_url: str | None
    start_s: float | None
    finish_s: float | None
    missing_reason: str | None
    absent_from_source: bool
    is_last_finisher: bool = False

    @property
    def drawable(self) -> bool:
        return self.missing_reason is None and self.start_s is not None and self.wall_s is not None


@dataclass(frozen=True)
class GanttPanel:
    snapshot: PipelineSnapshot
    origin: datetime | None
    origin_is_fallback: bool
    rows: tuple[GanttRow, ...]
    drawable: tuple[GanttRow, ...]
    unbuilt: tuple[GanttRow, ...]
    hidden_count: int
    x_min_s: float
    x_max_s: float


def _row(attempt: JobAttempt, origin: datetime | None) -> GanttRow:
    wall = None
    if attempt.started_at is not None and attempt.finished_at is not None:
        seconds = (attempt.finished_at - attempt.started_at).total_seconds()
        if seconds >= 0:
            wall = seconds
    if attempt.started_at is None and attempt.finished_at is None:
        reason = "missing start and finish timestamps"
    elif attempt.started_at is None:
        reason = "missing start timestamp"
    elif attempt.finished_at is None:
        reason = "missing finish timestamp"
    elif attempt.finished_at < attempt.started_at:
        reason = "finish before start"
    elif origin is None:
        reason = "pipeline origin is unknown"
    else:
        reason = None
    start_s = (
        (attempt.started_at - origin).total_seconds()
        if origin is not None and attempt.started_at is not None
        else None
    )
    finish_s = (
        (attempt.finished_at - origin).total_seconds()
        if origin is not None and attempt.finished_at is not None
        else None
    )
    return GanttRow(
        pipeline_id=attempt.pipeline_id,
        job_id=attempt.job_id,
        exact_name=attempt.exact_name,
        stage=attempt.stage,
        kind=attempt.kind,
        status=attempt.source_status,
        allow_failure=attempt.allow_failure,
        is_current_attempt=attempt.is_current_attempt,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
        wall_s=wall,
        gitlab_duration_s=attempt.gitlab_duration_s,
        queued_duration_s=attempt.queued_duration_s,
        web_url=attempt.web_url,
        start_s=start_s,
        finish_s=finish_s,
        missing_reason=reason,
        absent_from_source=not attempt.is_present,
    )


def _origin(snapshot: PipelineSnapshot) -> tuple[datetime | None, bool]:
    if snapshot.started_at is not None:
        return snapshot.started_at, False
    starts = [attempt.started_at for attempt in snapshot.attempts if attempt.started_at is not None]
    if not starts:
        return None, False
    return min(starts), True


def _stage_order(rows: Sequence[GanttRow]) -> dict[str, float]:
    starts: dict[str, float] = {}
    for row in rows:
        if row.start_s is None:
            continue
        starts[row.stage] = min(starts.get(row.stage, row.start_s), row.start_s)
    return starts


def _sort_value(row: GanttRow, sort_by: GanttSort) -> float:
    if sort_by == "finish":
        return row.finish_s or 0.0
    if sort_by == "duration":
        return row.wall_s or 0.0
    return row.start_s or 0.0


def _sort_rows(
    rows: Sequence[GanttRow], options: GanttOptions, stage_order: dict[str, float]
) -> tuple[GanttRow, ...]:
    def row_key(row: GanttRow) -> tuple[float, int]:
        return _sort_value(row, options.sort_by), row.job_id

    if options.sort_scope == "global":
        return tuple(sorted(rows, key=row_key, reverse=options.descending))
    grouped: dict[str, list[GanttRow]] = defaultdict(list)
    for row in rows:
        grouped[row.stage].append(row)
    ordered: list[GanttRow] = []
    for stage in sorted(grouped, key=lambda value: stage_order.get(value, 0.0)):
        ordered.extend(sorted(grouped[stage], key=row_key, reverse=options.descending))
    return tuple(ordered)


def _range(rows: Sequence[GanttRow]) -> tuple[float, float]:
    if not rows:
        return 0.0, 1.0
    starts = [row.start_s for row in rows if row.start_s is not None]
    finishes = [row.finish_s for row in rows if row.finish_s is not None]
    x_min = min([0.0, *starts])
    x_max = max(finishes, default=x_min + 1.0)
    if x_max <= x_min:
        x_max = x_min + 1.0
    return x_min, x_max


def build_gantt_panel(snapshot: PipelineSnapshot, options: GanttOptions) -> GanttPanel:
    origin, fallback = _origin(snapshot)
    rows = tuple(_row(attempt, origin) for attempt in snapshot.attempts)
    drawable = tuple(row for row in rows if row.drawable)
    unbuilt = tuple(row for row in rows if not row.drawable)

    search = options.search.strip().lower()
    visible = [
        row
        for row in drawable
        if row.stage not in options.hidden_stages
        and row.job_id not in options.hidden_job_ids
        and (not search or search in row.exact_name.lower())
    ]
    stage_order = _stage_order(drawable)
    ordered = _sort_rows(visible, options, stage_order)
    last_finisher_id = None
    if len(drawable) > 1:
        last_finisher_id = max(drawable, key=lambda row: (row.finish_s or 0.0, row.job_id)).job_id
    if last_finisher_id is not None:
        ordered = tuple(
            replace(row, is_last_finisher=True) if row.job_id == last_finisher_id else row
            for row in ordered
        )

    x_min, x_max = _range(drawable)
    if options.x_min_s is not None and options.x_max_s is not None:
        x_min, x_max = options.x_min_s, options.x_max_s
    return GanttPanel(
        snapshot=snapshot,
        origin=origin,
        origin_is_fallback=fallback,
        rows=ordered,
        drawable=drawable,
        unbuilt=unbuilt,
        hidden_count=len(drawable) - len(ordered),
        x_min_s=x_min,
        x_max_s=x_max,
    )


def shared_range(panels: Sequence[GanttPanel], options: GanttOptions) -> tuple[float, float]:
    if options.x_min_s is not None and options.x_max_s is not None:
        return options.x_min_s, options.x_max_s
    if not panels:
        return 0.0, 1.0
    x_min = min(panel.x_min_s for panel in panels)
    x_max = max(panel.x_max_s for panel in panels)
    if x_max <= x_min:
        x_max = x_min + 1.0
    return x_min, x_max


def gantt_height(panels: Sequence[GanttPanel], options: GanttOptions) -> int:
    total = 0
    for panel in panels:
        headers = len({row.stage for row in panel.rows}) if options.sort_scope == "stage" else 0
        total += max(220, 90 + (len(panel.rows) + headers) * 24)
    total += 80 if len(panels) > 1 else 0
    return total


def _legend_label(row: GanttRow) -> str:
    if row.status == "failed" and row.allow_failure:
        return "failed (allowed)"
    if row.status in _STATUS_COLORS:
        return row.status
    return "other"


def _hover(row: GanttRow, zone: ZoneInfo) -> str:
    parts = [
        f"<b>{escape_text(row.exact_name)}</b>",
        f"stage: {escape_text(row.stage)}",
        f"job id: {row.job_id} · {row.kind.value}",
        "status: " + escape_text(row.status) + (" (allow_failure)" if row.allow_failure else ""),
        f"start: {format_datetime(row.started_at, zone)}",
        f"finish: {format_datetime(row.finished_at, zone)}",
        f"wall: {format_seconds(row.wall_s)}",
        f"queued: {format_seconds(row.queued_duration_s)}",
        f"GitLab duration: {format_seconds(row.gitlab_duration_s)}",
    ]
    if row.absent_from_source:
        parts.append("not present in the latest API response")
    if row.web_url:
        parts.append(escape_text(row.web_url))
    return "<br>".join(parts)


def _labels(
    panel: GanttPanel, options: GanttOptions
) -> tuple[list[str], dict[int, int], list[int]]:
    labels: list[str] = []
    positions: dict[int, int] = {}
    headers: list[int] = []
    if options.sort_scope == "global":
        for index, row in enumerate(panel.rows):
            labels.append(escape_text(f"{row.stage} · {row.exact_name}"))
            positions[row.job_id] = index
        return labels, positions, headers

    duplicate_names = Counter((row.stage, row.exact_name) for row in panel.rows)
    groups: dict[str, list[GanttRow]] = defaultdict(list)
    for row in panel.rows:
        groups[row.stage].append(row)
    next_y = 0
    for stage, rows in groups.items():
        headers.append(next_y)
        labels.append(escape_text(stage))
        next_y += 1
        for row in rows:
            name = row.exact_name
            if duplicate_names[(row.stage, row.exact_name)] > 1:
                name = f"{name} [{row.job_id}]"
            labels.append(escape_text(name))
            positions[row.job_id] = next_y
            next_y += 1
    return labels, positions, headers


def _axis_ref(row_index: int, axis: str) -> str:
    return axis if row_index == 1 else f"{axis}{row_index}"


def build_gantt_figure(
    panels: Sequence[GanttPanel],
    options: GanttOptions,
    *,
    text: ChartText,
    export: ExportConfig,
    zone: ZoneInfo,
    height: int | None = None,
) -> go.Figure:
    count = max(1, len(panels))
    titles = [
        f"Pipeline {panel.snapshot.pipeline_id} · "
        f"{format_datetime(panel.snapshot.created_at, zone)} · {panel.snapshot.source_status}"
        for panel in panels
    ]
    figure = make_subplots(
        rows=count,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12 if count > 1 else 0.0,
        subplot_titles=titles,
    )
    x_min, x_max = shared_range(panels, options)
    tickvals, ticktext = nice_ticks(x_max)

    for row_index, panel in enumerate(panels, start=1):
        labels, positions, headers = _labels(panel, options)
        traces: dict[str, list[tuple[GanttRow, int]]] = defaultdict(list)
        for row in panel.rows:
            traces[_legend_label(row)].append((row, positions[row.job_id]))
        ordered_labels = [label for label in _LEGEND_ORDER if label in traces]
        ordered_labels.extend(sorted(set(traces) - set(_LEGEND_ORDER)))
        for label in ordered_labels:
            items = traces[label]
            color = _STATUS_COLORS.get(label, SERIOUS)
            marker: dict[str, object] = {"color": color}
            if label == "failed (allowed)":
                marker["line"] = {"color": INK, "width": 2}
            figure.add_trace(
                go.Bar(
                    x=[item.wall_s for item, _ in items],
                    base=[item.start_s for item, _ in items],
                    y=[position for _, position in items],
                    orientation="h",
                    name=label,
                    marker=marker,
                    customdata=[[_hover(item, zone)] for item, _ in items],
                    hovertemplate="%{customdata[0]}<extra></extra>",
                    showlegend=True,
                ),
                row=row_index,
                col=1,
            )
        for header in headers:
            figure.add_shape(
                type="line",
                x0=x_min,
                x1=x_max,
                y0=header + 0.5,
                y1=header + 0.5,
                line={"color": GRID, "width": 1},
                xref=_axis_ref(row_index, "x"),
                yref=_axis_ref(row_index, "y"),
            )
        last = next((row for row in panel.rows if row.is_last_finisher), None)
        if last is not None and last.finish_s is not None:
            figure.add_annotation(
                x=last.finish_s,
                y=positions[last.job_id],
                text="Finished last",
                showarrow=True,
                arrowhead=2,
                ax=40,
                ay=-18,
                font={"size": 10, "color": INK_SECONDARY},
                bgcolor=SURFACE,
                row=row_index,
                col=1,
            )
        figure.update_xaxes(
            range=[x_min, x_max],
            tickvals=tickvals,
            ticktext=ticktext,
            gridcolor=GRID,
            linecolor=AXIS,
            row=row_index,
            col=1,
        )
        figure.update_yaxes(
            tickvals=list(range(len(labels))),
            ticktext=labels,
            autorange="reversed",
            automargin=True,
            linecolor=AXIS,
            row=row_index,
            col=1,
        )

    default_title = "Gantt · " + " vs ".join(
        f"pipeline {panel.snapshot.pipeline_id}" for panel in panels
    )
    figure = style_figure(
        figure,
        text=text,
        default_title=default_title,
        default_x_title="Elapsed time from pipeline start",
        default_y_title="Attempt",
        export=export,
        height=height or gantt_height(panels, options),
    )
    figure.update_layout(
        showlegend=True,
        legend={"orientation": "h", "y": 1.02, "x": 1, "xanchor": "right"},
        barmode="overlay",
        bargap=0.25,
    )
    for annotation in figure.layout.annotations:
        if annotation.text in titles:
            annotation.font = {"size": 12, "color": INK_SECONDARY}

    hidden = sum(panel.hidden_count for panel in panels)
    unbuilt = sum(len(panel.unbuilt) for panel in panels)
    notes = []
    if hidden:
        notes.append(f"{hidden} rows hidden or filtered (display only)")
    if unbuilt:
        notes.append(f"{unbuilt} attempts not plotted: missing or invalid timestamps")
    if any(panel.origin_is_fallback for panel in panels):
        notes.append("pipeline start is missing; elapsed time uses the first job start")
    if len(panels) > 1:
        notes.append("two concrete pipelines, not period averages")
    if notes:
        figure.update_layout(margin={"b": 100})
        figure.add_annotation(
            x=0,
            y=0,
            xref="paper",
            yref="paper",
            yshift=-75,
            text=" · ".join(notes),
            showarrow=False,
            xanchor="left",
            align="left",
            font={"size": 10, "color": MUTED},
        )
    return figure


def row_table(panels: Sequence[GanttPanel], zone: ZoneInfo) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for panel in panels:
        for row in (*panel.rows, *panel.unbuilt):
            rows.append(
                {
                    "pipeline": row.pipeline_id,
                    "job_id": row.job_id,
                    "stage": row.stage,
                    "job": row.exact_name,
                    "kind": row.kind.value,
                    "status": row.status,
                    "allow_failure": row.allow_failure,
                    "start": format_datetime(row.started_at, zone),
                    "finish": format_datetime(row.finished_at, zone),
                    "wall_s": row.wall_s,
                    "queued_s": row.queued_duration_s,
                    "gitlab_s": row.gitlab_duration_s,
                    "finish_offset_s": row.finish_s,
                    "plotted": row.drawable,
                    "reason": row.missing_reason or "",
                    "url": row.web_url or "",
                }
            )
    return rows
