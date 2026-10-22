from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import pytest

from glabviz.analysis_config import ChartText, ExportConfig
from glabviz.domain import AttemptKind
from glabviz.formatting import escape_text, format_seconds, nice_ticks
from glabviz.gantt import (
    GanttOptions,
    build_gantt_figure,
    build_gantt_panel,
    gantt_height,
    row_table,
    shared_range,
)
from tests.factories import attempt, snapshot

ZONE = ZoneInfo("UTC")
START = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
TEXT = ChartText()
EXPORT = ExportConfig(width=1600, height=900)


def _pipeline(*attempts, started_at=START, finished_at=None):
    if finished_at is None and started_at is not None:
        finished_at = started_at + timedelta(hours=2)
    return snapshot(
        pipeline_id=1,
        started_at=started_at,
        finished_at=finished_at,
        created_at=started_at or START,
        attempts=attempts,
    )


def _job(job_id: int, name: str, stage: str, start_min: int, duration_min: int, **kwargs):
    start = START + timedelta(minutes=start_min)
    return attempt(
        job_id=job_id,
        pipeline_id=1,
        name=name,
        stage=stage,
        start=start,
        finish=start + timedelta(minutes=duration_min),
        **kwargs,
    )


def _bars(fig: go.Figure) -> list[tuple[float, float]]:
    result = []
    for trace in fig.data:
        if not isinstance(trace, go.Bar):
            continue
        for base, duration in zip(trace.base, trace.x, strict=True):
            result.append((float(base), float(duration)))
    return sorted(result)


def _snapshot_with_two_jobs():
    return _pipeline(
        _job(1, "build", "build", 1, 2),
        _job(2, "unit", "test", 5, 5),
    )


def test_bars_match_attempt_start_and_duration():
    panel = build_gantt_panel(_snapshot_with_two_jobs(), GanttOptions())
    figure = build_gantt_figure([panel], GanttOptions(), text=TEXT, export=EXPORT, zone=ZONE)
    assert _bars(figure) == [(60.0, 120.0), (300.0, 300.0)]
    assert figure.layout.xaxis.range == (0, 600)


def test_failed_and_success_attempts_are_separate_rows():
    snap = _pipeline(
        _job(1, "unit", "test", 1, 2, status="failed"),
        _job(2, "unit", "test", 4, 2, status="success"),
    )
    panel = build_gantt_panel(snap, GanttOptions())
    assert [row.job_id for row in panel.rows] == [1, 2]
    assert _bars(
        build_gantt_figure([panel], GanttOptions(), text=TEXT, export=EXPORT, zone=ZONE)
    ) == [(60.0, 120.0), (240.0, 120.0)]
    table = row_table([panel], ZONE)
    assert [row["job_id"] for row in table] == [1, 2]


def test_hiding_an_early_job_does_not_move_the_remaining_bar_or_rescale():
    snap = _snapshot_with_two_jobs()
    full = build_gantt_panel(snap, GanttOptions())
    hidden = build_gantt_panel(snap, GanttOptions(hidden_stages=frozenset({"build"})))
    remaining = [row for row in hidden.rows if row.job_id == 2][0]
    assert remaining.start_s == 300
    assert (hidden.x_min_s, hidden.x_max_s) == (full.x_min_s, full.x_max_s)
    figure = build_gantt_figure(
        [hidden],
        GanttOptions(hidden_stages=frozenset({"build"})),
        text=TEXT,
        export=EXPORT,
        zone=ZONE,
    )
    assert _bars(figure) == [(300.0, 300.0)]
    assert any("rows hidden" in annotation.text for annotation in figure.layout.annotations)


def test_hiding_a_late_job_does_not_rescale_the_axis():
    snap = _snapshot_with_two_jobs()
    full = build_gantt_panel(snap, GanttOptions())
    hidden = build_gantt_panel(snap, GanttOptions(hidden_stages=frozenset({"test"})))
    assert (hidden.x_min_s, hidden.x_max_s) == (full.x_min_s, full.x_max_s)


def test_search_filters_display_only_and_keeps_the_axis():
    snap = _snapshot_with_two_jobs()
    full = build_gantt_panel(snap, GanttOptions())
    searched = build_gantt_panel(snap, GanttOptions(search="unit"))
    assert [row.job_id for row in searched.rows] == [2]
    assert (searched.x_min_s, searched.x_max_s) == (full.x_min_s, full.x_max_s)
    assert searched.hidden_count == 1


def test_two_panels_share_the_same_x_range():
    first = build_gantt_panel(_snapshot_with_two_jobs(), GanttOptions())
    second_attempt = attempt(
        job_id=10,
        pipeline_id=1,
        name="deploy",
        stage="deploy",
        start=datetime(2026, 5, 2, 10, 2, tzinfo=UTC),
        finish=datetime(2026, 5, 2, 10, 32, tzinfo=UTC),
    )
    second_snapshot = snapshot(
        pipeline_id=1,
        started_at=datetime(2026, 5, 2, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        created_at=datetime(2026, 5, 2, 10, 0, tzinfo=UTC),
        attempts=(second_attempt,),
    )
    second = build_gantt_panel(second_snapshot, GanttOptions())
    options = GanttOptions()
    assert shared_range([first, second], options) == (0.0, 1920.0)
    figure = build_gantt_figure([first, second], options, text=TEXT, export=EXPORT, zone=ZONE)
    assert figure.layout.xaxis.range == figure.layout.xaxis2.range


def test_long_durations_do_not_wrap_at_24_hours():
    long_job = _job(1, "e2e", "e2e", 0, 43 * 60 + 48)
    panel = build_gantt_panel(_pipeline(long_job), GanttOptions())
    figure = build_gantt_figure([panel], GanttOptions(), text=TEXT, export=EXPORT, zone=ZONE)
    labels = list(figure.layout.xaxis.ticktext)
    assert panel.x_max_s == pytest.approx(43 * 3600 + 48 * 60)
    assert format_seconds(panel.x_max_s) == "1d 19:48"
    assert any("d " in label for label in labels)
    assert "19:48" not in labels


def test_missing_timestamps_are_listed_and_not_drawn():
    snap = _pipeline(
        _job(1, "build", "build", 1, 2),
        attempt(job_id=2, pipeline_id=1, name="broken", stage="test", start=None, finish=None),
    )
    panel = build_gantt_panel(snap, GanttOptions())
    assert len(panel.rows) == 1
    assert len(panel.unbuilt) == 1
    assert panel.unbuilt[0].missing_reason is not None
    figure = build_gantt_figure([panel], GanttOptions(), text=TEXT, export=EXPORT, zone=ZONE)
    assert _bars(figure) == [(60.0, 120.0)]
    assert any("not plotted" in annotation.text for annotation in figure.layout.annotations)


def test_fallback_origin_is_reported():
    attempt_row = _job(1, "build", "build", 1, 2)
    snap = _pipeline(attempt_row, started_at=None)
    panel = build_gantt_panel(snap, GanttOptions())
    assert panel.origin_is_fallback is True
    figure = build_gantt_figure([panel], GanttOptions(), text=TEXT, export=EXPORT, zone=ZONE)
    assert any("first job start" in annotation.text for annotation in figure.layout.annotations)


def test_duplicate_job_names_get_distinct_labels():
    snap = _pipeline(
        _job(1, "unit", "test", 1, 2),
        _job(2, "unit", "test", 4, 2),
    )
    panel = build_gantt_panel(snap, GanttOptions())
    figure = build_gantt_figure([panel], GanttOptions(), text=TEXT, export=EXPORT, zone=ZONE)
    labels = list(figure.layout.yaxis.ticktext)
    assert "unit [1]" in labels
    assert "unit [2]" in labels


def test_names_are_full_and_escaped():
    name = "test integration beta <&> " + "x" * 40
    snap = _pipeline(_job(1, name, "test", 1, 2))
    panel = build_gantt_panel(snap, GanttOptions())
    figure = build_gantt_figure([panel], GanttOptions(), text=TEXT, export=EXPORT, zone=ZONE)
    labels = list(figure.layout.yaxis.ticktext)
    assert any(escape_text(name) in label for label in labels)
    assert all("<&>" not in label for label in labels)


def test_global_sort_labels_include_the_stage():
    snap = _snapshot_with_two_jobs()
    options = GanttOptions(sort_scope="global", sort_by="duration", descending=True)
    panel = build_gantt_panel(snap, options)
    figure = build_gantt_figure([panel], options, text=TEXT, export=EXPORT, zone=ZONE)
    labels = list(figure.layout.yaxis.ticktext)
    assert labels == ["test · unit", "build · build"]


def test_row_table_has_sortable_columns_and_links():
    snap = _pipeline(_job(1, "build", "build", 1, 2))
    panel = build_gantt_panel(snap, GanttOptions())
    table = row_table([panel], ZONE)
    assert table[0]["wall_s"] == pytest.approx(120)
    assert table[0]["finish_offset_s"] == pytest.approx(180)
    assert table[0]["queued_s"] == pytest.approx(0)
    assert table[0]["plotted"] is True


def test_bridge_attempt_keeps_its_kind():
    bridge = attempt(
        job_id=9,
        pipeline_id=1,
        name="run_e2e",
        stage="e2e",
        kind=AttemptKind.BRIDGE,
        start=START,
        finish=START + timedelta(minutes=10),
    )
    panel = build_gantt_panel(_pipeline(bridge), GanttOptions())
    assert panel.rows[0].kind is AttemptKind.BRIDGE
    assert row_table([panel], ZONE)[0]["kind"] == "bridge"


def test_nice_ticks_cover_the_shared_range():
    values, labels = nice_ticks(600)
    assert values[0] == 0
    assert values[-1] >= 600
    assert len(values) == len(labels)


def test_finished_last_is_not_reassigned_when_the_true_last_is_hidden():
    snap = _pipeline(
        _job(1, "a", "test", 0, 2),
        _job(2, "b", "test", 2, 8),
        _job(3, "c", "test", 3, 5),
    )
    full = build_gantt_panel(snap, GanttOptions())
    assert [row.job_id for row in full.rows if row.is_last_finisher] == [2]
    hidden = build_gantt_panel(snap, GanttOptions(hidden_job_ids=frozenset({2})))
    assert not any(row.is_last_finisher for row in hidden.rows)


def test_gantt_height_grows_with_the_visible_rows():
    attempts = tuple(_job(index + 1, f"job {index}", "test", index, 1) for index in range(120))
    panel = build_gantt_panel(_pipeline(*attempts), GanttOptions())
    assert gantt_height([panel], GanttOptions()) > 2400
