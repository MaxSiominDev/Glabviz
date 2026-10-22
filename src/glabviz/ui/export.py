from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from glabviz.analysis_config import ChartText, ExportConfig
from glabviz.charts import plotly_config
from glabviz.ui import keys

_DEFAULTS = {
    "failure": ChartText(
        "Runs with failures", "glabviz-runs-with-failures", "Period", "Failure rate"
    ),
    "wall": ChartText("Wall-clock duration", "glabviz-wall-clock", "Period", "Wall-clock"),
    "distribution": ChartText(
        "Wall-clock distribution", "glabviz-wall-distribution", "Wall-clock", "Period"
    ),
    "pending": ChartText("Job queue wait", "glabviz-pending", "Period", "Pending"),
    "dynamics": ChartText("Metric over time", "glabviz-dynamics", "Period bucket", "Metric"),
    "gantt": ChartText("", "glabviz-gantt", "Elapsed time from pipeline start", "Attempt"),
}


def default_text(chart_id: str) -> ChartText:
    return _DEFAULTS[chart_id]


def render_export_settings() -> ExportConfig:
    st.session_state.setdefault(keys.E_FORMAT, "png")
    st.session_state.setdefault(keys.E_WIDTH, 1200)
    st.session_state.setdefault(keys.E_HEIGHT, 700)
    st.session_state.setdefault(keys.E_FONT_SIZE, 12)
    st.session_state.setdefault(keys.E_SCALE, 2.0)
    with st.expander("Export settings", expanded=False):
        left, right = st.columns(2)
        image_format = left.selectbox("Format", ["png", "svg"], key=keys.E_FORMAT)
        width = right.number_input(
            "Width (px)",
            min_value=200,
            max_value=10_000,
            step=100,
            key=keys.E_WIDTH,
        )
        height = right.number_input(
            "Height (px)",
            min_value=200,
            max_value=20_000,
            step=100,
            key=keys.E_HEIGHT,
        )
        font_size = left.number_input(
            "Font size",
            min_value=6,
            max_value=72,
            step=1,
            key=keys.E_FONT_SIZE,
        )
        scale = left.number_input(
            "PNG scale",
            min_value=0.5,
            max_value=10.0,
            step=0.5,
            format="%.1f",
            key=keys.E_SCALE,
        )
        st.caption(
            "The camera icon in each chart toolbar downloads the figure. SVG ignores the PNG scale."
        )
    return ExportConfig(
        image_format=image_format,  # type: ignore[arg-type]
        width=int(width),
        height=int(height),
        font_size=int(font_size),
        scale=float(scale),
    )


def render_chart_export(chart_id: str, default: ChartText | None = None) -> ChartText:
    values = default or default_text(chart_id)
    for field, value in (
        ("title", values.title),
        ("filename", values.filename),
        ("x", values.x_label),
        ("y", values.y_label),
    ):
        st.session_state.setdefault(keys.chart_key(chart_id, field), value)
    with st.expander("Export title, file name and axis labels", expanded=False):
        title = st.text_input("Title", key=keys.chart_key(chart_id, "title"))
        filename = st.text_input("File name", key=keys.chart_key(chart_id, "filename"))
        left, right = st.columns(2)
        x_label = left.text_input("X axis label", key=keys.chart_key(chart_id, "x"))
        y_label = right.text_input("Y axis label", key=keys.chart_key(chart_id, "y"))
    return ChartText(title=title, filename=filename, x_label=x_label, y_label=y_label)


def collect_chart_texts() -> dict[str, ChartText]:
    result = {}
    for chart_id in keys.CHART_IDS:
        values = default_text(chart_id)
        result[chart_id] = ChartText(
            title=st.session_state.get(keys.chart_key(chart_id, "title"), values.title),
            filename=st.session_state.get(keys.chart_key(chart_id, "filename"), values.filename),
            x_label=st.session_state.get(keys.chart_key(chart_id, "x"), values.x_label),
            y_label=st.session_state.get(keys.chart_key(chart_id, "y"), values.y_label),
        )
    return result


def render_figure(
    figure: go.Figure,
    chart_id: str,
    text: ChartText,
    export: ExportConfig,
    *,
    height: int | None = None,
) -> None:
    st.plotly_chart(
        figure,
        theme=None,
        config=plotly_config(export, text, f"glabviz-{chart_id}", height=height),
        key=f"plotly_{chart_id}",
        width="stretch",
    )
