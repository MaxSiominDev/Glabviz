from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go

from glabviz.analysis import DynamicsPoint
from glabviz.analysis_config import ChartText, ExportConfig, PendingMetric, WallMetric
from glabviz.formatting import (
    MISSING,
    format_percent,
    format_percent_points,
    format_seconds,
    format_signed_seconds,
    nice_ticks,
    safe_filename,
)
from glabviz.metrics import FailureRate, PendingResult, Stats, stat_value

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES_BEFORE = "#2a78d6"
SERIES_AFTER = "#eb6834"
GOOD = "#0ca30c"
CRITICAL = "#d03b3b"


def _empty_note(fig: go.Figure, text: str) -> None:
    fig.add_annotation(
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        text=text,
        showarrow=False,
        font={"size": 13, "color": MUTED},
    )


def annotate_note(fig: go.Figure, note: str, *, y: float = -0.12) -> go.Figure:
    if not note:
        return fig
    fig.add_annotation(
        x=0,
        y=y,
        xref="paper",
        yref="paper",
        text=note,
        showarrow=False,
        xanchor="left",
        align="left",
        font={"size": 10, "color": MUTED},
    )
    fig.update_layout(margin_b=100)
    return fig


def style_figure(
    fig: go.Figure,
    *,
    text: ChartText,
    default_title: str,
    default_x_title: str = "",
    default_y_title: str = "",
    export: ExportConfig,
    height: int | None = None,
) -> go.Figure:
    title = text.title or default_title
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "x": 0.01, "xanchor": "left", "font": {"size": 16}},
        font={
            "family": "system-ui, -apple-system, Segoe UI, sans-serif",
            "size": export.font_size,
            "color": INK,
        },
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin={"l": 70, "r": 30, "t": 60, "b": 60},
        height=height or export.height,
        width=export.width,
        hoverlabel={"bgcolor": SURFACE, "font": {"color": INK}},
        showlegend=False,
    )
    fig.update_xaxes(
        title_text=text.x_label or default_x_title,
        gridcolor=GRID,
        linecolor=AXIS,
        tickfont={"color": INK_SECONDARY},
    )
    fig.update_yaxes(
        title_text=text.y_label or default_y_title,
        gridcolor=GRID,
        linecolor=AXIS,
        tickfont={"color": INK_SECONDARY},
    )
    return fig


def plotly_config(
    export: ExportConfig,
    text: ChartText,
    fallback_filename: str,
    *,
    height: int | None = None,
) -> dict[str, Any]:
    filename = safe_filename(text.filename or fallback_filename)
    return {
        "displayModeBar": True,
        "displaylogo": False,
        "responsive": True,
        "doubleClick": "reset",
        "modeBarButtonsToRemove": [
            "sendChartToCloud",
            "sendDataToCloud",
            "lasso2d",
            "select2d",
        ],
        "toImageButtonOptions": {
            "format": export.image_format,
            "filename": filename,
            "width": export.width,
            "height": height or export.height,
            "scale": export.scale,
        },
    }


def failure_rate_chart(
    before: FailureRate,
    after: FailureRate,
    *,
    before_label: str,
    after_label: str,
    text: ChartText,
    export: ExportConfig,
) -> go.Figure:
    rates = (before, after)
    values = [rate.percent if rate.percent is not None else 0.0 for rate in rates]
    labels = [
        f"{rate.red}/{rate.denominator}<br>{format_percent(rate.value)}"
        if rate.value is not None
        else MISSING
        for rate in rates
    ]
    hovertext = [
        (
            f"failure rate {format_percent(rate.value)}<br>"
            f"red {rate.red}, green {rate.green}, outside {rate.outside_fail}, "
            f"denominator {rate.denominator}"
            if rate.value is not None
            else MISSING
        )
        for rate in rates
    ]
    fig = go.Figure(
        go.Bar(
            x=[before_label, after_label],
            y=values,
            text=labels,
            textposition="outside",
            cliponaxis=False,
            marker_color=[SERIES_BEFORE, SERIES_AFTER],
            hovertext=hovertext,
            hovertemplate="%{x}<br>%{hovertext}<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="Failure rate", range=[0, 100], ticksuffix="%")
    fig = style_figure(
        fig,
        text=text,
        default_title="Runs with failures",
        default_x_title="Period",
        default_y_title="Failure rate",
        export=export,
    )
    if all(rate.value is None for rate in rates):
        _empty_note(fig, "No data")
    return fig


def duration_chart(
    before: Stats,
    after: Stats,
    *,
    metric: WallMetric,
    before_label: str,
    after_label: str,
    text: ChartText,
    export: ExportConfig,
) -> go.Figure:
    stats = (before, after)
    values = [stat_value(item, metric) for item in stats]
    labels = [
        f"{format_seconds(value)}<br>n={item.n}" if value is not None else MISSING
        for value, item in zip(values, stats, strict=True)
    ]
    fig = go.Figure(
        go.Bar(
            x=[before_label, after_label],
            y=[value if value is not None else 0.0 for value in values],
            text=labels,
            textposition="outside",
            cliponaxis=False,
            marker_color=[SERIES_BEFORE, SERIES_AFTER],
            customdata=[[item.n, item.n] for item in stats],
            hovertemplate="%{x}<br>%{text}<br>n=%{customdata[0]}<extra></extra>",
        )
    )
    maximum = max((value for value in values if value is not None), default=0.0)
    tickvals, ticktext = nice_ticks(maximum * 1.15 if maximum else 1.0)
    fig.update_yaxes(title_text="Wall-clock", tickvals=tickvals, ticktext=ticktext)
    fig = style_figure(
        fig,
        text=text,
        default_title="Wall-clock duration",
        default_x_title="Period",
        default_y_title="Wall-clock",
        export=export,
    )
    if all(value is None for value in values):
        _empty_note(fig, "No green observations")
    return fig


def distribution_chart(
    before_values: Sequence[float],
    after_values: Sequence[float],
    *,
    before_label: str,
    after_label: str,
    text: ChartText,
    export: ExportConfig,
) -> go.Figure:
    fig = go.Figure()
    for values, label, color in (
        (before_values, before_label, SERIES_BEFORE),
        (after_values, after_label, SERIES_AFTER),
    ):
        if not values:
            continue
        fig.add_trace(
            go.Box(
                x=list(values),
                name=label,
                orientation="h",
                marker_color=color,
                line={"width": 2},
                boxpoints="outliers",
                boxmean=True,
                hovertemplate=f"{label}<br>%{{x}} s<extra></extra>",
            )
        )
    maximum = max([*before_values, *after_values], default=0.0)
    tickvals, ticktext = nice_ticks(maximum * 1.15 if maximum else 1.0)
    fig.update_xaxes(title_text="Wall-clock", tickvals=tickvals, ticktext=ticktext)
    fig.update_yaxes(title_text="Period")
    fig = style_figure(
        fig,
        text=text,
        default_title="Wall-clock distribution (green observations)",
        default_x_title="Wall-clock",
        default_y_title="Period",
        export=export,
    )
    fig.update_layout(showlegend=False)
    if not before_values and not after_values:
        _empty_note(fig, "No green observations")
    return fig


def pending_chart(
    before: PendingResult,
    after: PendingResult,
    *,
    metric: PendingMetric,
    before_label: str,
    after_label: str,
    text: ChartText,
    export: ExportConfig,
) -> go.Figure:
    results = (before, after)
    values = [stat_value(item.stats, metric) for item in results]
    labels = [
        f"{format_seconds(value) if value is not None else MISSING}"
        f"<br>{item.known}/{item.expected} known"
        for value, item in zip(values, results, strict=True)
    ]
    fig = go.Figure(
        go.Bar(
            x=[before_label, after_label],
            y=[value if value is not None else 0.0 for value in values],
            text=labels,
            textposition="outside",
            cliponaxis=False,
            marker_color=[SERIES_BEFORE, SERIES_AFTER],
            customdata=[[item.known, item.expected] for item in results],
            hovertemplate=(
                "%{x}<br>%{text}<br>coverage %{customdata[0]}/%{customdata[1]}<extra></extra>"
            ),
        )
    )
    maximum = max((value for value in values if value is not None), default=0.0)
    tickvals, ticktext = nice_ticks(maximum * 1.15 if maximum else 1.0)
    fig.update_yaxes(title_text="Pending", tickvals=tickvals, ticktext=ticktext)
    fig = style_figure(
        fig,
        text=text,
        default_title="Job queue wait",
        default_x_title="Period",
        default_y_title="Pending",
        export=export,
    )
    if all(value is None for value in values):
        _empty_note(fig, "No known queue durations")
    return fig


def dynamics_chart(
    points: Sequence[DynamicsPoint],
    *,
    before_label: str,
    after_label: str,
    metric_label: str,
    is_percent: bool,
    text: ChartText,
    export: ExportConfig,
) -> go.Figure:
    fig = go.Figure()
    for attribute, label, color in (
        ("before", before_label, SERIES_BEFORE),
        ("after", after_label, SERIES_AFTER),
    ):
        values = [getattr(point, attribute) for point in points]
        counts = [getattr(point, f"{attribute}_n") for point in points]
        if is_percent:
            hover = [
                f"{label}<br>{point.start_date}<br>"
                f"{format_percent(value) if value is not None else MISSING}<br>n={count}"
                for point, value, count in zip(points, values, counts, strict=True)
            ]
            y_values = [None if value is None else value * 100 for value in values]
        else:
            hover = [
                f"{label}<br>{point.start_date}<br>"
                f"{format_seconds(value) if value is not None else MISSING}<br>n={count}"
                for point, value, count in zip(points, values, counts, strict=True)
            ]
            y_values = values
        fig.add_trace(
            go.Scatter(
                x=[point.start_date for point in points],
                y=y_values,
                name=label,
                mode="lines+markers",
                connectgaps=False,
                line={"width": 2, "color": color},
                marker={"size": 8, "color": color},
                text=hover,
                hovertemplate="%{text}<extra></extra>",
            )
        )
    fig.update_xaxes(title_text="Period bucket", type="date")
    if is_percent:
        fig.update_yaxes(title_text=metric_label, ticksuffix="%", range=[0, 100])
    else:
        maximum = max(
            (value for point in points for value in (point.before, point.after) if value),
            default=0.0,
        )
        tickvals, ticktext = nice_ticks(maximum * 1.15 if maximum else 1.0)
        fig.update_yaxes(title_text=metric_label, tickvals=tickvals, ticktext=ticktext)
    fig = style_figure(
        fig,
        text=text,
        default_title=f"{metric_label} over time",
        default_x_title="Period bucket",
        default_y_title=metric_label,
        export=export,
    )
    fig.update_layout(showlegend=True, legend={"orientation": "h", "y": 1.02, "x": 1})
    if all(point.before is None and point.after is None for point in points):
        _empty_note(fig, "No data in the selected buckets")
    return fig


def delta_caption(absolute: float | None, relative_percent: float | None) -> str:
    if absolute is None:
        return MISSING
    relative = f" ({relative_percent:+.1f}%)" if relative_percent is not None else ""
    return f"{format_signed_seconds(absolute)}{relative}"


def rate_delta_caption(delta_pp: float | None, relative_percent: float | None) -> str:
    if delta_pp is None:
        return MISSING
    relative = f" ({relative_percent:+.1f}%)" if relative_percent is not None else ""
    return f"{format_percent_points(delta_pp)}{relative}"
