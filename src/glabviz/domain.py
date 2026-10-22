from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EXECUTED_STATUSES = frozenset({"success", "failed"})
TERMINAL_STATUSES = frozenset({"success", "failed", "canceled", "skipped"})
SETTLING_STATUSES = frozenset(
    {
        "created",
        "waiting_for_resource",
        "preparing",
        "pending",
        "running",
        "scheduled",
    }
)


class AttemptKind(StrEnum):
    JOB = "job"
    BRIDGE = "bridge"


class SourcePresence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"


class SnapshotCompleteness(StrEnum):
    COMPLETE = "complete"
    INCONSISTENT = "inconsistent"
    BRIDGES_UNSUPPORTED = "bridges_unsupported"


class ObjectCategory(StrEnum):
    RED = "red"
    GREEN = "green"
    OUTSIDE_FAIL = "outside_fail"
    CANCELED = "canceled"
    NO_EXECUTION = "no_execution"
    UNEXPLAINED_FAILED = "unexplained_failed"
    NOT_SETTLED = "not_settled"
    INSUFFICIENT_DATA = "insufficient_data"
    OTHER = "other"


class ExclusionReason(StrEnum):
    NOT_GREEN = "not_green"
    NO_EXECUTED_ATTEMPTS = "no_executed_attempts"
    MISSING_TIMESTAMPS = "missing_timestamps"
    NEGATIVE_INTERVAL = "negative_interval"
    NEGATIVE_QUEUED_DURATION = "negative_queued_duration"
    NULL_QUEUED_DURATION = "null_queued_duration"
    AMBIGUOUS_PARALLEL_JOBS = "ambiguous_parallel_jobs"


@dataclass(frozen=True)
class ProjectInfo:
    project_id: int
    path: str
    name: str | None
    default_branch: str | None


@dataclass(frozen=True)
class JobAttempt:
    job_id: int
    pipeline_id: int
    exact_name: str
    stage: str
    kind: AttemptKind
    source_status: str
    allow_failure: bool
    observed_failure: bool
    is_current_attempt: bool | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    gitlab_duration_s: float | None
    queued_duration_s: float | None
    runner_id: int | None
    web_url: str | None
    failure_reason: str | None
    source_presence: SourcePresence = SourcePresence.PRESENT

    @property
    def counted_failure(self) -> bool:
        return self.observed_failure and not self.allow_failure

    @property
    def is_present(self) -> bool:
        return self.source_presence is SourcePresence.PRESENT

    @property
    def is_executed(self) -> bool:
        return self.is_present and self.source_status in EXECUTED_STATUSES


@dataclass(frozen=True)
class PipelineSnapshot:
    instance_base_url: str
    project_id: int
    pipeline_id: int
    iid: int | None
    name: str | None
    ref: str | None
    source: str | None
    sha: str | None
    source_status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime | None
    gitlab_duration_s: float | None
    web_url: str | None
    attempts: tuple[JobAttempt, ...]
    completeness: SnapshotCompleteness
    snapshot_error: str | None = None
    last_attempt_state: str | None = None
    last_error: str | None = None

    @property
    def source_discrepancy(self) -> bool:
        return any(a.source_presence is SourcePresence.ABSENT for a in self.attempts)

    @property
    def is_trustworthy(self) -> bool:
        return self.completeness is SnapshotCompleteness.COMPLETE and not self.source_discrepancy

    @property
    def is_stale(self) -> bool:
        if self.completeness is not SnapshotCompleteness.COMPLETE:
            return False
        state = self.last_attempt_state
        return (state is not None and state != SnapshotCompleteness.COMPLETE.value) or bool(
            self.last_error
        )


@dataclass(frozen=True)
class MetricPeriod:
    start_date: date
    end_date: date
    timezone: str

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("period end date must not be before start date")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def utc_bounds(self) -> tuple[datetime, datetime]:
        zone = self.zone
        start = datetime.combine(self.start_date, time.min, tzinfo=zone)
        end = datetime.combine(self.end_date + timedelta(days=1), time.min, tzinfo=zone)
        return (
            start.astimezone(UTC),
            end.astimezone(UTC),
        )

    def contains(self, value: datetime) -> bool:
        start, end = self.utc_bounds()
        return start <= value.astimezone(UTC) < end

    def label(self) -> str:
        if self.start_date == self.end_date:
            return f"{self.start_date.isoformat()} ({self.timezone})"
        return f"{self.start_date.isoformat()}..{self.end_date.isoformat()} ({self.timezone})"


def parse_gitlab_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
