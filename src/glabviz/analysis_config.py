from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONFIG_VERSION = 1

AnalysisLevel = Literal["pipeline", "stage", "job"]
WallMetric = Literal["mean", "median", "p95", "max"]
PendingMetric = Literal["mean", "median", "p95"]
DynamicsMetric = Literal[
    "failure_rate",
    "wall_mean",
    "wall_median",
    "wall_p95",
    "wall_max",
    "pending_mean",
    "pending_median",
    "pending_p95",
]
Granularity = Literal["day", "week", "month"]
ImageFormat = Literal["png", "svg"]
GanttSort = Literal["start", "finish", "duration"]
SortScope = Literal["stage", "global"]

LEVELS = ("pipeline", "stage", "job")
WALL_METRICS = ("mean", "median", "p95", "max")
PENDING_METRICS = ("mean", "median", "p95")
DYNAMICS_METRICS = (
    "failure_rate",
    "wall_mean",
    "wall_median",
    "wall_p95",
    "wall_max",
    "pending_mean",
    "pending_median",
    "pending_p95",
)
GRANULARITIES = ("day", "week", "month")
IMAGE_FORMATS = ("png", "svg")
GANTT_SORTS = ("start", "finish", "duration")
SORT_SCOPES = ("stage", "global")

MAX_TEXT = 300
MAX_FILENAME = 200
MAX_HIDDEN = 10_000
MAX_SCOPE_STAGES = 500
MAX_CHART_TEXTS = 20


class ConfigValidationError(ValueError):
    pass


class ConfigTooNewError(ConfigValidationError):
    pass


@dataclass(frozen=True)
class ProjectRef:
    instance_base_url: str
    project_id: int


@dataclass(frozen=True)
class PipelineFilters:
    source: str | None = None
    ref: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class PeriodConfig:
    start_date: date
    end_date: date
    timezone: str = "UTC"


@dataclass(frozen=True)
class ComparisonConfig:
    level: AnalysisLevel = "pipeline"
    stage: str | None = None
    job_stage: str | None = None
    job_name: str | None = None
    scope_stages: tuple[str, ...] = ()
    wall_metric: WallMetric = "mean"
    pending_metric: PendingMetric = "mean"
    dynamics_metric: DynamicsMetric = "failure_rate"
    granularity: Granularity = "week"
    integration_prefix: str = "test integration"


@dataclass(frozen=True)
class ChartText:
    title: str = ""
    filename: str = ""
    x_label: str = ""
    y_label: str = ""


@dataclass(frozen=True)
class ExportConfig:
    image_format: ImageFormat = "png"
    width: int = 1200
    height: int = 700
    font_size: int = 12
    scale: float = 2.0
    chart_texts: dict[str, ChartText] = field(default_factory=dict)


@dataclass(frozen=True)
class GanttConfig:
    primary_pipeline_id: int | None = None
    secondary_pipeline_id: int | None = None
    hidden_stages: tuple[str, ...] = ()
    hidden_job_ids: tuple[int, ...] = ()
    sort_by: GanttSort = "start"
    sort_descending: bool = False
    sort_scope: SortScope = "stage"
    x_min_s: float | None = None
    x_max_s: float | None = None


@dataclass(frozen=True)
class SavedAnalysisConfig:
    project: ProjectRef
    before: PeriodConfig
    after: PeriodConfig
    comparison: ComparisonConfig = field(default_factory=ComparisonConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    gantt: GanttConfig = field(default_factory=GanttConfig)
    filters: PipelineFilters = field(default_factory=PipelineFilters)


def _fail(path: str, message: str) -> ConfigValidationError:
    return ConfigValidationError(f"{path}: {message}")


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(path, "must be an object")
    for key in value:
        if not isinstance(key, str):
            raise _fail(path, "object keys must be strings")
    return value


def _check_keys(mapping: dict[str, Any], path: str, required: set[str], optional: set[str]) -> None:
    unknown = set(mapping) - required - optional
    if unknown:
        raise _fail(path, f"unknown keys: {sorted(unknown)}")
    missing = required - set(mapping)
    if missing:
        raise _fail(path, f"missing keys: {sorted(missing)}")


def _string(value: object, path: str, *, allow_empty: bool = False, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise _fail(path, "must be a string")
    text = value.strip()
    if not allow_empty and not text:
        raise _fail(path, "must not be empty")
    if len(text) > maximum:
        raise _fail(path, f"must be at most {maximum} characters")
    return text


def _optional_string(value: object, path: str, *, maximum: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    return _string(value, path, allow_empty=True, maximum=maximum)


def _int(value: object, path: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(path, "must be an integer")
    if value < minimum:
        raise _fail(path, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise _fail(path, f"must be at most {maximum}")
    return value


def _optional_int(value: object, path: str, *, minimum: int) -> int | None:
    if value is None:
        return None
    return _int(value, path, minimum=minimum)


def _float(value: object, path: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(path, "must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise _fail(path, "must be finite")
    if number < minimum or number > maximum:
        raise _fail(path, f"must be between {minimum} and {maximum}")
    return number


def _optional_float(value: object, path: str) -> float | None:
    if value is None:
        return None
    return _float(value, path, minimum=-1e9, maximum=1e9)


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, "must be a boolean")
    return value


def _enum(value: object, path: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _fail(path, f"must be one of {list(allowed)}")
    return value


def _date(value: object, path: str) -> date:
    if not isinstance(value, str):
        raise _fail(path, "must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _fail(path, "must be an ISO date") from exc


def _filename(value: object, path: str) -> str:
    text = _string(value, path, allow_empty=True, maximum=MAX_FILENAME)
    if any(character in text for character in ("/", "\\")):
        raise _fail(path, "must not contain path separators")
    return text


def _list_of_strings(value: object, path: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(path, "must be a list")
    if len(value) > maximum:
        raise _fail(path, f"must contain at most {maximum} items")
    result = tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise _fail(path, "must not contain duplicates")
    return result


def _list_of_ints(value: object, path: str, *, maximum: int) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise _fail(path, "must be a list")
    if len(value) > maximum:
        raise _fail(path, f"must contain at most {maximum} items")
    result = tuple(_int(item, f"{path}[{index}]", minimum=1) for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise _fail(path, "must not contain duplicates")
    return result


def _parse_project(value: object) -> ProjectRef:
    mapping = _mapping(value, "project")
    _check_keys(mapping, "project", {"instance_base_url", "project_id"}, set())
    base_url = _string(mapping["instance_base_url"], "project.instance_base_url")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise _fail("project.instance_base_url", "must be an http or https URL with a host")
    return ProjectRef(
        instance_base_url=base_url.rstrip("/"),
        project_id=_int(mapping["project_id"], "project.project_id", minimum=1),
    )


def _parse_filters(value: object) -> PipelineFilters:
    mapping = _mapping(value, "filters")
    _check_keys(mapping, "filters", {"source", "ref", "name"}, set())
    return PipelineFilters(
        source=_optional_string(mapping["source"], "filters.source"),
        ref=_optional_string(mapping["ref"], "filters.ref"),
        name=_optional_string(mapping["name"], "filters.name"),
    )


def _parse_period(value: object, path: str) -> PeriodConfig:
    mapping = _mapping(value, path)
    _check_keys(mapping, path, {"start_date", "end_date", "timezone"}, set())
    period = PeriodConfig(
        start_date=_date(mapping["start_date"], f"{path}.start_date"),
        end_date=_date(mapping["end_date"], f"{path}.end_date"),
        timezone=_string(mapping["timezone"], f"{path}.timezone", maximum=100),
    )
    try:
        ZoneInfo(period.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _fail(f"{path}.timezone", "unknown IANA timezone") from exc
    if period.end_date < period.start_date:
        raise _fail(path, "end_date must not be before start_date")
    return period


def _parse_comparison(value: object) -> ComparisonConfig:
    mapping = _mapping(value, "comparison")
    required = {
        "level",
        "stage",
        "job_stage",
        "job_name",
        "scope_stages",
        "wall_metric",
        "pending_metric",
        "dynamics_metric",
        "granularity",
        "integration_prefix",
    }
    _check_keys(mapping, "comparison", required, set())
    level = _enum(mapping["level"], "comparison.level", LEVELS)
    stage = _optional_string(mapping["stage"], "comparison.stage")
    job_stage = _optional_string(mapping["job_stage"], "comparison.job_stage")
    job_name = _optional_string(mapping["job_name"], "comparison.job_name")
    if level == "stage" and stage is None:
        raise _fail("comparison.stage", "is required for stage level")
    if level == "job" and (job_stage is None or job_name is None):
        raise _fail("comparison.job_stage/job_name", "are required for job level")
    return ComparisonConfig(
        level=level,  # type: ignore[arg-type]
        stage=stage,
        job_stage=job_stage,
        job_name=job_name,
        scope_stages=_list_of_strings(
            mapping["scope_stages"], "comparison.scope_stages", maximum=MAX_SCOPE_STAGES
        ),
        wall_metric=_enum(mapping["wall_metric"], "comparison.wall_metric", WALL_METRICS),  # type: ignore[arg-type]
        pending_metric=_enum(
            mapping["pending_metric"], "comparison.pending_metric", PENDING_METRICS
        ),  # type: ignore[arg-type]
        dynamics_metric=_enum(
            mapping["dynamics_metric"], "comparison.dynamics_metric", DYNAMICS_METRICS
        ),  # type: ignore[arg-type]
        granularity=_enum(mapping["granularity"], "comparison.granularity", GRANULARITIES),  # type: ignore[arg-type]
        integration_prefix=_string(
            mapping["integration_prefix"],
            "comparison.integration_prefix",
            allow_empty=True,
        ),
    )


def _parse_chart_text(value: object, path: str) -> ChartText:
    mapping = _mapping(value, path)
    _check_keys(mapping, path, {"title", "filename", "x_label", "y_label"}, set())
    return ChartText(
        title=_string(mapping["title"], f"{path}.title", allow_empty=True),
        filename=_filename(mapping["filename"], f"{path}.filename"),
        x_label=_string(mapping["x_label"], f"{path}.x_label", allow_empty=True),
        y_label=_string(mapping["y_label"], f"{path}.y_label", allow_empty=True),
    )


def _parse_export(value: object) -> ExportConfig:
    mapping = _mapping(value, "export")
    _check_keys(
        mapping,
        "export",
        {"format", "width", "height", "font_size", "scale", "chart_texts"},
        set(),
    )
    texts_value = _mapping(mapping["chart_texts"], "export.chart_texts")
    if len(texts_value) > MAX_CHART_TEXTS:
        raise _fail("export.chart_texts", f"must contain at most {MAX_CHART_TEXTS} entries")
    texts = {
        _string(key, "export.chart_texts key", maximum=100): _parse_chart_text(
            item, f"export.chart_texts.{key}"
        )
        for key, item in texts_value.items()
    }
    return ExportConfig(
        image_format=_enum(mapping["format"], "export.format", IMAGE_FORMATS),  # type: ignore[arg-type]
        width=_int(mapping["width"], "export.width", minimum=200, maximum=10_000),
        height=_int(mapping["height"], "export.height", minimum=200, maximum=20_000),
        font_size=_int(mapping["font_size"], "export.font_size", minimum=6, maximum=72),
        scale=_float(mapping["scale"], "export.scale", minimum=0.5, maximum=10.0),
        chart_texts=texts,
    )


def _parse_gantt(value: object) -> GanttConfig:
    mapping = _mapping(value, "gantt")
    required = {
        "primary_pipeline_id",
        "secondary_pipeline_id",
        "hidden_stages",
        "hidden_job_ids",
        "sort_by",
        "sort_descending",
        "sort_scope",
        "x_min_s",
        "x_max_s",
    }
    _check_keys(mapping, "gantt", required, set())
    x_min = _optional_float(mapping["x_min_s"], "gantt.x_min_s")
    x_max = _optional_float(mapping["x_max_s"], "gantt.x_max_s")
    if (x_min is None) != (x_max is None):
        raise _fail("gantt.x_min_s/x_max_s", "must both be set or both be empty")
    if x_min is not None and x_max is not None and x_min >= x_max:
        raise _fail("gantt.x_min_s/x_max_s", "x_min_s must be less than x_max_s")
    return GanttConfig(
        primary_pipeline_id=_optional_int(
            mapping["primary_pipeline_id"], "gantt.primary_pipeline_id", minimum=1
        ),
        secondary_pipeline_id=_optional_int(
            mapping["secondary_pipeline_id"], "gantt.secondary_pipeline_id", minimum=1
        ),
        hidden_stages=_list_of_strings(
            mapping["hidden_stages"], "gantt.hidden_stages", maximum=MAX_SCOPE_STAGES
        ),
        hidden_job_ids=_list_of_ints(
            mapping["hidden_job_ids"], "gantt.hidden_job_ids", maximum=MAX_HIDDEN
        ),
        sort_by=_enum(mapping["sort_by"], "gantt.sort_by", GANTT_SORTS),  # type: ignore[arg-type]
        sort_descending=_bool(mapping["sort_descending"], "gantt.sort_descending"),
        sort_scope=_enum(mapping["sort_scope"], "gantt.sort_scope", SORT_SCOPES),  # type: ignore[arg-type]
        x_min_s=x_min,
        x_max_s=x_max,
    )


def parse_config(payload: object) -> SavedAnalysisConfig:
    mapping = _mapping(payload, "config")
    _check_keys(
        mapping,
        "config",
        {"version", "project", "filters", "periods", "comparison", "export", "gantt"},
        set(),
    )
    version = _int(mapping["version"], "config.version", minimum=1)
    if version > CONFIG_VERSION:
        raise ConfigTooNewError(
            f"config version {version} is newer than supported {CONFIG_VERSION}"
        )
    if version < CONFIG_VERSION:
        raise ConfigValidationError(f"config version {version} is not supported")
    periods = _mapping(mapping["periods"], "periods")
    _check_keys(periods, "periods", {"before", "after"}, set())
    return SavedAnalysisConfig(
        project=_parse_project(mapping["project"]),
        filters=_parse_filters(mapping["filters"]),
        before=_parse_period(periods["before"], "periods.before"),
        after=_parse_period(periods["after"], "periods.after"),
        comparison=_parse_comparison(mapping["comparison"]),
        export=_parse_export(mapping["export"]),
        gantt=_parse_gantt(mapping["gantt"]),
    )


def _reject_constant(value: str) -> None:
    raise ConfigValidationError(f"invalid JSON constant: {value}")


def config_from_json(text: str) -> SavedAnalysisConfig:
    try:
        payload = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"config is not valid JSON: {exc}") from exc
    return parse_config(payload)


def _period_payload(period: PeriodConfig) -> dict[str, object]:
    return {
        "start_date": period.start_date.isoformat(),
        "end_date": period.end_date.isoformat(),
        "timezone": period.timezone,
    }


def config_payload(config: SavedAnalysisConfig) -> dict[str, object]:
    comparison = config.comparison
    export = config.export
    gantt = config.gantt
    return {
        "version": CONFIG_VERSION,
        "project": {
            "instance_base_url": config.project.instance_base_url,
            "project_id": config.project.project_id,
        },
        "filters": {
            "source": config.filters.source,
            "ref": config.filters.ref,
            "name": config.filters.name,
        },
        "periods": {
            "before": _period_payload(config.before),
            "after": _period_payload(config.after),
        },
        "comparison": {
            "level": comparison.level,
            "stage": comparison.stage,
            "job_stage": comparison.job_stage,
            "job_name": comparison.job_name,
            "scope_stages": list(comparison.scope_stages),
            "wall_metric": comparison.wall_metric,
            "pending_metric": comparison.pending_metric,
            "dynamics_metric": comparison.dynamics_metric,
            "granularity": comparison.granularity,
            "integration_prefix": comparison.integration_prefix,
        },
        "export": {
            "format": export.image_format,
            "width": export.width,
            "height": export.height,
            "font_size": export.font_size,
            "scale": export.scale,
            "chart_texts": {
                key: {
                    "title": text.title,
                    "filename": text.filename,
                    "x_label": text.x_label,
                    "y_label": text.y_label,
                }
                for key, text in sorted(export.chart_texts.items())
            },
        },
        "gantt": {
            "primary_pipeline_id": gantt.primary_pipeline_id,
            "secondary_pipeline_id": gantt.secondary_pipeline_id,
            "hidden_stages": list(gantt.hidden_stages),
            "hidden_job_ids": list(gantt.hidden_job_ids),
            "sort_by": gantt.sort_by,
            "sort_descending": gantt.sort_descending,
            "sort_scope": gantt.sort_scope,
            "x_min_s": gantt.x_min_s,
            "x_max_s": gantt.x_max_s,
        },
    }


def dump_config(config: SavedAnalysisConfig) -> str:
    return json.dumps(config_payload(config), sort_keys=True, ensure_ascii=False, allow_nan=False)


def validate_analysis_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ConfigValidationError("analysis name must not be empty")
    if len(name) > MAX_TEXT:
        raise ConfigValidationError(f"analysis name must be at most {MAX_TEXT} characters")
    return name
