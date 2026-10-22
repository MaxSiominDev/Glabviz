from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from glabviz.analysis_config import (
    ChartText,
    ComparisonConfig,
    ConfigTooNewError,
    ConfigValidationError,
    ExportConfig,
    GanttConfig,
    PeriodConfig,
    PipelineFilters,
    ProjectRef,
    SavedAnalysisConfig,
    config_from_json,
    dump_config,
    parse_config,
    validate_analysis_name,
)


def _config() -> SavedAnalysisConfig:
    return SavedAnalysisConfig(
        project=ProjectRef("https://gitlab.example.com", 1),
        filters=PipelineFilters(source="merge_request_event"),
        before=PeriodConfig(date(2026, 5, 1), date(2026, 5, 31), "UTC"),
        after=PeriodConfig(date(2026, 10, 1), date(2026, 10, 18), "UTC"),
        comparison=ComparisonConfig(
            level="job",
            job_stage="checks",
            job_name="test integration alpha",
            scope_stages=("checks",),
            wall_metric="p95",
            pending_metric="median",
            dynamics_metric="wall_p95",
            granularity="day",
        ),
        export=ExportConfig(
            image_format="svg",
            width=1400,
            height=800,
            font_size=14,
            scale=3.0,
            chart_texts={"wall": ChartText(title="Wall", filename="wall", x_label="s")},
        ),
        gantt=GanttConfig(
            primary_pipeline_id=10,
            secondary_pipeline_id=20,
            hidden_stages=("deploy",),
            hidden_job_ids=(100, 200),
            sort_by="duration",
            sort_descending=True,
            sort_scope="global",
            x_min_s=0.0,
            x_max_s=7200.0,
        ),
    )


def test_config_roundtrip_is_lossless():
    config = _config()
    assert config_from_json(dump_config(config)) == config


def test_config_keeps_unicode_and_special_characters():
    config = replace(_config(), filters=PipelineFilters(ref="feature/café <&>"))
    assert config_from_json(dump_config(config)) == config


def test_unknown_keys_are_rejected():
    payload = json.loads(dump_config(_config()))
    payload["unexpected"] = True
    with pytest.raises(ConfigValidationError, match="unknown keys"):
        parse_config(payload)


def test_future_config_version_is_rejected():
    payload = json.loads(dump_config(_config()))
    payload["version"] = 2
    with pytest.raises(ConfigTooNewError):
        parse_config(payload)


def test_version_must_be_an_integer_not_a_boolean():
    payload = json.loads(dump_config(_config()))
    payload["version"] = True
    with pytest.raises(ConfigValidationError, match="must be an integer"):
        parse_config(payload)


def test_invalid_date_and_timezone_are_rejected():
    payload = json.loads(dump_config(_config()))
    payload["periods"]["before"]["start_date"] = "not-a-date"
    with pytest.raises(ConfigValidationError, match="ISO date"):
        parse_config(payload)

    payload = json.loads(dump_config(_config()))
    payload["periods"]["after"]["timezone"] = "Mars/Olympus"
    with pytest.raises(ConfigValidationError, match="IANA timezone"):
        parse_config(payload)

    payload = json.loads(dump_config(_config()))
    payload["periods"]["after"]["timezone"] = "../x"
    with pytest.raises(ConfigValidationError, match="IANA timezone"):
        parse_config(payload)


def test_project_url_requires_a_host():
    payload = json.loads(dump_config(_config()))
    payload["project"]["instance_base_url"] = "https://"
    with pytest.raises(ConfigValidationError, match="host"):
        parse_config(payload)


def test_period_end_before_start_is_rejected():
    payload = json.loads(dump_config(_config()))
    payload["periods"]["before"]["end_date"] = "2026-04-01"
    with pytest.raises(ConfigValidationError, match="end_date"):
        parse_config(payload)


def test_level_requires_its_objects():
    payload = json.loads(dump_config(_config()))
    payload["comparison"]["level"] = "stage"
    payload["comparison"]["stage"] = None
    with pytest.raises(ConfigValidationError, match="stage level"):
        parse_config(payload)

    payload = json.loads(dump_config(_config()))
    payload["comparison"]["level"] = "job"
    payload["comparison"]["job_name"] = None
    with pytest.raises(ConfigValidationError, match="job level"):
        parse_config(payload)


def test_invalid_enums_and_bounds_are_rejected():
    payload = json.loads(dump_config(_config()))
    payload["comparison"]["granularity"] = "year"
    with pytest.raises(ConfigValidationError, match="must be one of"):
        parse_config(payload)

    payload = json.loads(dump_config(_config()))
    payload["export"]["width"] = 10
    with pytest.raises(ConfigValidationError, match="between|at least"):
        parse_config(payload)

    payload = json.loads(dump_config(_config()))
    payload["export"]["scale"] = float("inf")
    with pytest.raises(ConfigValidationError, match="finite|between"):
        parse_config(payload)


def test_filename_cannot_escape_a_directory():
    payload = json.loads(dump_config(_config()))
    payload["export"]["chart_texts"]["wall"]["filename"] = "../secret"
    with pytest.raises(ConfigValidationError, match="path separators"):
        parse_config(payload)


def test_gantt_range_must_be_consistent():
    payload = json.loads(dump_config(_config()))
    payload["gantt"]["x_max_s"] = None
    with pytest.raises(ConfigValidationError, match="both"):
        parse_config(payload)

    payload = json.loads(dump_config(_config()))
    payload["gantt"]["x_min_s"] = 10.0
    payload["gantt"]["x_max_s"] = 5.0
    with pytest.raises(ConfigValidationError, match="less than"):
        parse_config(payload)


def test_json_constants_are_rejected():
    with pytest.raises(ConfigValidationError):
        config_from_json('{"version": NaN}')


def test_analysis_name_is_trimmed_and_validated():
    assert validate_analysis_name("  May vs October  ") == "May vs October"
    with pytest.raises(ConfigValidationError):
        validate_analysis_name("   ")


def test_an_empty_integration_prefix_roundtrips_as_disabled():
    config = _config()
    config = replace(
        config,
        comparison=replace(config.comparison, integration_prefix=""),
    )
    assert config_from_json(dump_config(config)) == config
