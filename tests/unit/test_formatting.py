from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from glabviz.formatting import (
    format_datetime,
    format_percent,
    format_percent_points,
    format_rate,
    format_seconds,
    format_signed_seconds,
    nice_ticks,
    safe_filename,
)
from glabviz.metrics import FailureRate


def test_format_seconds_handles_units_and_missing():
    assert format_seconds(None) == "No data"
    assert format_seconds(0) == "0s"
    assert format_seconds(45) == "45s"
    assert format_seconds(90) == "1m 30s"
    assert format_seconds(3672) == "1h 01m"
    assert format_seconds(157_680) == "1d 19:48"
    assert format_seconds(-90) == "-1m 30s"


def test_signed_seconds_adds_a_plus_for_positive_deltas():
    assert format_signed_seconds(90) == "+1m 30s"
    assert format_signed_seconds(-90) == "-1m 30s"
    assert format_signed_seconds(None) == "No data"


def test_rate_formatting_keeps_numerator_and_denominator():
    rate = FailureRate(red=49, green=117, outside_fail=14)
    assert format_percent(rate.value) == "27.2%"
    assert format_rate(rate) == "27.2% (49/180)"
    empty = FailureRate(red=0, green=0, outside_fail=0)
    assert rate.value is not None
    assert empty.value is None
    assert format_rate(empty) == "No data"


def test_percent_points_are_not_percentages():
    assert format_percent_points(-65.0) == "-65.0 pp"
    assert format_percent_points(7.25) == "+7.2 pp"
    assert format_percent_points(None) == "No data"


def test_safe_filename_removes_extensions_and_paths():
    assert safe_filename("wall.png") == "wall"
    assert safe_filename("../a b/c.svg") == "a b-c"
    assert safe_filename("  ") == "glabviz"
    assert safe_filename("x" * 300) == "x" * 120


def test_nice_ticks_start_at_zero_and_render_seconds():
    values, labels = nice_ticks(100)
    assert values[0] == 0
    assert values[-1] >= 100
    assert labels[0] == "0s"
    short_values, short_labels = nice_ticks(1)
    assert short_values == [0.0, 1.0]
    assert short_labels == ["0s", "1s"]


def test_format_datetime_uses_the_requested_zone():
    value = datetime(2026, 5, 1, 12, 30, tzinfo=UTC)
    assert format_datetime(value, ZoneInfo("UTC")) == "2026-05-01 12:30"
    assert format_datetime(value, ZoneInfo("Europe/Moscow")) == "2026-05-01 15:30"
    assert format_datetime(None, ZoneInfo("UTC")) == "No data"
