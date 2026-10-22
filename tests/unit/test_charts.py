from __future__ import annotations

from datetime import date

import pytest

from glabviz.analysis import DynamicsPoint
from glabviz.analysis_config import ChartText, ExportConfig
from glabviz.charts import (
    annotate_note,
    delta_caption,
    distribution_chart,
    duration_chart,
    dynamics_chart,
    failure_rate_chart,
    pending_chart,
    plotly_config,
    rate_delta_caption,
    style_figure,
)
from glabviz.metrics import FailureRate, PendingResult, summarize

TEXT = ChartText(title="Custom title", filename="custom.png", x_label="X", y_label="Y")
EXPORT = ExportConfig(image_format="svg", width=1600, height=900, font_size=13, scale=3.0)


def _rate(red: int, green: int, outside: int = 0) -> FailureRate:
    return FailureRate(red=red, green=green, outside_fail=outside)


def test_failure_rate_chart_uses_percent_and_denominator_labels():
    fig = failure_rate_chart(
        _rate(49, 117, 14),
        _rate(0, 0),
        before_label="May",
        after_label="October",
        text=TEXT,
        export=EXPORT,
    )
    bar = fig.data[0]
    assert list(bar.y) == pytest.approx([49 / 180 * 100, 0.0])
    assert "49/180" in bar.text[0]
    assert bar.text[1] == "No data"
    assert fig.layout.yaxis.range == (0, 100)
    assert fig.layout.title.text == "Custom title"


def test_duration_chart_reads_the_selected_stat():
    before = summarize([60, 120])
    after = summarize([30])
    for metric, expected in (("mean", 90), ("median", 90), ("p95", 117), ("max", 120)):
        fig = duration_chart(
            before,
            after,
            metric=metric,
            before_label="A",
            after_label="B",
            text=ChartText(),
            export=EXPORT,
        )
        assert fig.data[0].y[0] == pytest.approx(expected)


def test_distribution_chart_keeps_both_samples_apart():
    fig = distribution_chart(
        [10, 20, 30],
        [5, 15],
        before_label="A",
        after_label="B",
        text=ChartText(),
        export=EXPORT,
    )
    assert len(fig.data) == 2
    assert list(fig.data[0].x) == [10, 20, 30]
    assert list(fig.data[1].x) == [5, 15]
    assert fig.data[0].name == "A"


def test_pending_chart_shows_coverage_and_missing_values():
    before = PendingResult(known=2, expected=3, stats=summarize([5, 15]))
    after = PendingResult(known=0, expected=0, stats=summarize([]))
    fig = pending_chart(
        before,
        after,
        metric="median",
        before_label="A",
        after_label="B",
        text=ChartText(),
        export=EXPORT,
    )
    assert fig.data[0].y[0] == pytest.approx(10)
    assert "2/3 known" in fig.data[0].text[0]
    assert "No data" in fig.data[0].text[1]


def test_dynamics_chart_keeps_gaps_and_percent_scale():
    points = (
        DynamicsPoint(date(2026, 5, 4), 1.0, None, 1, 0),
        DynamicsPoint(date(2026, 5, 11), None, None, 0, 0),
        DynamicsPoint(date(2026, 10, 5), None, 0.25, 0, 2),
    )
    fig = dynamics_chart(
        points,
        before_label="May",
        after_label="October",
        metric_label="Failure rate",
        is_percent=True,
        text=ChartText(),
        export=EXPORT,
    )
    assert len(fig.data) == 2
    assert fig.data[0].connectgaps is False
    assert list(fig.data[0].y) == [100.0, None, None]
    assert list(fig.data[1].y) == [None, None, 25.0]
    assert fig.layout.yaxis.range == (0, 100)


def test_plotly_config_sets_export_options_and_removes_cloud_buttons():
    config = plotly_config(EXPORT, TEXT, "fallback")
    options = config["toImageButtonOptions"]
    assert options == {
        "format": "svg",
        "filename": "custom",
        "width": 1600,
        "height": 900,
        "scale": 3.0,
    }
    assert "sendChartToCloud" in config["modeBarButtonsToRemove"]
    assert "sendDataToCloud" in config["modeBarButtonsToRemove"]
    assert config["displaylogo"] is False


def test_export_height_can_be_overridden_for_a_tall_chart():
    config = plotly_config(EXPORT, ChartText(), "fallback", height=1800)
    assert config["toImageButtonOptions"]["height"] == 1800


def test_style_figure_applies_editable_titles_and_sizes():
    import plotly.graph_objects as go

    fig = go.Figure()
    styled = style_figure(
        fig,
        text=ChartText(title="T", x_label="X", y_label="Y"),
        default_title="default",
        export=EXPORT,
    )
    assert styled.layout.title.text == "T"
    assert styled.layout.xaxis.title.text == "X"
    assert styled.layout.yaxis.title.text == "Y"
    assert styled.layout.width == 1600
    assert styled.layout.height == 900
    assert styled.layout.font.size == 13


def test_delta_captions_format_precomputed_percentages():
    assert delta_caption(-900, -50.0) == "-15m 00s (-50.0%)"
    assert delta_caption(None, None) == "No data"
    assert rate_delta_caption(-65.0, -92.857) == "-65.0 pp (-92.9%)"
    assert rate_delta_caption(None, None) == "No data"


def test_failure_rate_chart_notes_when_both_sides_have_no_data():
    fig = failure_rate_chart(
        _rate(0, 0),
        _rate(0, 0),
        before_label="A",
        after_label="B",
        text=ChartText(),
        export=EXPORT,
    )
    assert any("No data" in annotation.text for annotation in fig.layout.annotations)


def test_annotate_note_adds_a_period_caption_to_an_export():
    import plotly.graph_objects as go

    fig = annotate_note(go.Figure(), "A: 2026-05-01..2026-05-31; B: 2026-10-01..2026-10-18")
    assert any("2026-05-01..2026-05-31" in annotation.text for annotation in fig.layout.annotations)
    assert fig.layout.margin.b == 100
