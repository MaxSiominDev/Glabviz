from __future__ import annotations

import fcntl
import json
import shutil
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb

from glabviz.analysis_config import (
    CONFIG_VERSION,
    ConfigValidationError,
    SavedAnalysisConfig,
    config_from_json,
    dump_config,
    validate_analysis_name,
)
from glabviz.domain import (
    EXECUTED_STATUSES,
    AttemptKind,
    JobAttempt,
    PipelineSnapshot,
    ProjectInfo,
    SnapshotCompleteness,
    SourcePresence,
)

SCHEMA_VERSION = 2
METRICS_VERSION = "1"

_DB_FILENAME = "glabviz.duckdb"
_LOCK_FILENAME = ".glabviz.lock"
_MIGRATIONS = {1: "001_initial.sql", 2: "002_saved_analyses.sql"}


class StorageError(Exception):
    pass


class StorageLockedError(StorageError):
    pass


class SchemaTooNewError(StorageError):
    pass


class SavedAnalysisNameError(StorageError):
    pass


class SavedAnalysisNotFoundError(StorageError):
    pass


@dataclass(frozen=True)
class StoredProject:
    instance_base_url: str
    project_id: int
    path: str
    display_name: str | None
    default_branch: str | None
    default_timezone: str
    local_filter: dict[str, Any]
    fetched_at: datetime | None


@dataclass(frozen=True)
class Coverage:
    pipeline_count: int
    first_created_at: datetime | None
    last_created_at: datetime | None
    last_fetched_at: datetime | None
    last_successful_sync_at: datetime | None
    incomplete_pipelines: int


@dataclass(frozen=True)
class SyncRun:
    sync_id: str
    instance_base_url: str
    project_id: int
    requested_start: datetime
    requested_end: datetime
    requested_timezone: str
    requested_start_date: date
    requested_end_date: date
    started_at: datetime
    finished_at: datetime | None
    state: str
    discovered_count: int
    completed_count: int
    error_summary: str | None


@dataclass(frozen=True)
class SavedAnalysis:
    analysis_id: str
    name: str
    instance_base_url: str
    project_id: int
    config: SavedAnalysisConfig
    created_at: datetime
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _to_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _from_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC)


def _required_from_naive_utc(value: datetime | None) -> datetime:
    result = _from_naive_utc(value)
    if result is None:
        raise StorageError("required timestamp is missing")
    return result


def _timestamp_validity(attempt: JobAttempt) -> str:
    if attempt.source_status not in EXECUTED_STATUSES:
        return "not_executed"
    if attempt.started_at is None and attempt.finished_at is None:
        return "missing_both"
    if attempt.started_at is None:
        return "missing_start"
    if attempt.finished_at is None:
        return "missing_finish"
    if attempt.finished_at < attempt.started_at:
        return "negative"
    return "valid"


class Storage:
    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        lock_file: Any,
        db_path: Path,
    ) -> None:
        self._conn = connection
        self._lock_file = lock_file
        self._db_path = db_path
        self._lock = threading.RLock()

    @classmethod
    def open(cls, directory: Path) -> Storage:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "logs").mkdir(exist_ok=True)
        (directory / "backups").mkdir(exist_ok=True)
        lock_file = (directory / _LOCK_FILENAME).open("a+")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise StorageLockedError(
                f"database directory is already in use by another process: {directory}"
            ) from exc
        db_path = directory / _DB_FILENAME
        connection = duckdb.connect(str(db_path))
        storage = cls(connection, lock_file, db_path)
        try:
            storage._initialize()
        except BaseException:
            storage.close()
            raise
        return storage

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]
            if self._lock_file is not None:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
                self._lock_file = None

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS app_meta "
                "(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)"
            )
            version = self._meta_int("schema_version", 0)
            if version > SCHEMA_VERSION:
                raise SchemaTooNewError(
                    f"database schema version {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version < SCHEMA_VERSION:
                if version > 0:
                    self._backup()
                for target in range(version + 1, SCHEMA_VERSION + 1):
                    filename = _MIGRATIONS[target]
                    sql = (
                        resources.files("glabviz")
                        .joinpath("migrations", filename)
                        .read_text(encoding="utf-8")
                    )
                    self._conn.execute(sql)
                    self._set_meta("schema_version", str(target))
            self._set_meta("metrics_version", METRICS_VERSION)
            if self._meta_int("data_revision", -1) < 0:
                self._set_meta("data_revision", "0")

    def _backup(self) -> None:
        self._conn.execute("CHECKPOINT")
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        target = self._db_path.parent / "backups" / f"{self._db_path.stem}-{stamp}.duckdb"
        shutil.copy2(self._db_path, target)

    def _meta_int(self, key: str, default: int) -> int:
        row = self._conn.execute("SELECT value FROM app_meta WHERE key = ?", [key]).fetchone()
        if row is None:
            return default
        try:
            return int(row[0])
        except TypeError, ValueError:
            return default

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            [key, value],
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._conn.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def _increment_data_revision(self) -> None:
        self._conn.execute(
            "UPDATE app_meta SET value = CAST(CAST(value AS BIGINT) + 1 AS VARCHAR) "
            "WHERE key = 'data_revision'"
        )

    def data_revision(self) -> int:
        with self._lock:
            return self._meta_int("data_revision", 0)

    def upsert_project(
        self,
        project: ProjectInfo,
        instance_base_url: str,
        timezone: str,
        local_filter: dict[str, Any],
        fetched_at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO projects (
                    instance_base_url, project_id, path, display_name, default_branch,
                    default_timezone, local_filter_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (instance_base_url, project_id) DO UPDATE SET
                    path = excluded.path,
                    display_name = excluded.display_name,
                    default_branch = excluded.default_branch,
                    default_timezone = excluded.default_timezone,
                    local_filter_json = excluded.local_filter_json,
                    fetched_at = excluded.fetched_at
                """,
                [
                    instance_base_url,
                    project.project_id,
                    project.path,
                    project.name,
                    project.default_branch,
                    timezone,
                    json.dumps(local_filter, sort_keys=True),
                    _to_naive_utc(fetched_at or _now()),
                ],
            )

    def projects(self) -> list[StoredProject]:
        with self._lock:
            rows = self._fetch_rows(
                "SELECT * FROM projects ORDER BY path, instance_base_url, project_id", []
            )
        result = []
        for row in rows:
            try:
                parsed_filter = json.loads(row["local_filter_json"])
            except TypeError, ValueError:
                parsed_filter = {}
            local_filter = parsed_filter if isinstance(parsed_filter, dict) else {}
            result.append(
                StoredProject(
                    instance_base_url=row["instance_base_url"],
                    project_id=int(row["project_id"]),
                    path=row["path"],
                    display_name=row["display_name"],
                    default_branch=row["default_branch"],
                    default_timezone=row["default_timezone"],
                    local_filter=local_filter,
                    fetched_at=_from_naive_utc(row["fetched_at"]),
                )
            )
        return result

    def publish_snapshot(self, snapshot: PipelineSnapshot) -> None:
        now = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT snapshot_completeness FROM pipelines "
                "WHERE instance_base_url = ? AND project_id = ? AND pipeline_id = ?",
                [snapshot.instance_base_url, snapshot.project_id, snapshot.pipeline_id],
            ).fetchone()
            if (
                existing is not None
                and existing[0] == SnapshotCompleteness.COMPLETE.value
                and snapshot.completeness is not SnapshotCompleteness.COMPLETE
            ):
                self._record_pipeline_attempt(snapshot, now)
                return
            with self._transaction():
                self._upsert_pipeline(snapshot, now)
                self._upsert_attempts(snapshot, now)
                self._mark_absent(snapshot)
                self._increment_data_revision()

    def _record_pipeline_attempt(self, snapshot: PipelineSnapshot, now: datetime) -> None:
        self._conn.execute(
            "UPDATE pipelines SET last_attempt_at = ?, last_attempt_state = ?, last_error = ? "
            "WHERE instance_base_url = ? AND project_id = ? AND pipeline_id = ?",
            [
                _to_naive_utc(now),
                snapshot.completeness.value,
                snapshot.snapshot_error,
                snapshot.instance_base_url,
                snapshot.project_id,
                snapshot.pipeline_id,
            ],
        )
        self._increment_data_revision()

    def record_pipeline_error(
        self,
        instance_base_url: str,
        project_id: int,
        pipeline_id: int,
        message: str,
        at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pipelines SET last_attempt_at = ?, last_attempt_state = 'failed', "
                "last_error = ? WHERE instance_base_url = ? AND project_id = ? AND pipeline_id = ?",
                [
                    _to_naive_utc(at or _now()),
                    message,
                    instance_base_url,
                    project_id,
                    pipeline_id,
                ],
            )
            self._increment_data_revision()

    def _upsert_pipeline(self, snapshot: PipelineSnapshot, now: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO pipelines (
                instance_base_url, project_id, pipeline_id, iid, name, ref, source, sha,
                source_status, created_at, started_at, finished_at, updated_at,
                gitlab_duration_s, web_url, fetched_at, snapshot_completeness, snapshot_error,
                first_seen_at, last_seen_at, last_attempt_at, last_attempt_state, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (instance_base_url, project_id, pipeline_id) DO UPDATE SET
                iid = excluded.iid,
                name = excluded.name,
                ref = excluded.ref,
                source = excluded.source,
                sha = excluded.sha,
                source_status = excluded.source_status,
                created_at = excluded.created_at,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                updated_at = excluded.updated_at,
                gitlab_duration_s = excluded.gitlab_duration_s,
                web_url = excluded.web_url,
                fetched_at = excluded.fetched_at,
                snapshot_completeness = excluded.snapshot_completeness,
                snapshot_error = excluded.snapshot_error,
                last_seen_at = excluded.last_seen_at,
                last_attempt_at = excluded.last_attempt_at,
                last_attempt_state = excluded.last_attempt_state,
                last_error = excluded.last_error
            """,
            [
                snapshot.instance_base_url,
                snapshot.project_id,
                snapshot.pipeline_id,
                snapshot.iid,
                snapshot.name,
                snapshot.ref,
                snapshot.source,
                snapshot.sha,
                snapshot.source_status,
                _to_naive_utc(snapshot.created_at),
                _to_naive_utc(snapshot.started_at),
                _to_naive_utc(snapshot.finished_at),
                _to_naive_utc(snapshot.updated_at),
                snapshot.gitlab_duration_s,
                snapshot.web_url,
                _to_naive_utc(now),
                snapshot.completeness.value,
                snapshot.snapshot_error,
                _to_naive_utc(now),
                _to_naive_utc(now),
                _to_naive_utc(now),
                snapshot.completeness.value,
                snapshot.snapshot_error,
            ],
        )

    def _upsert_attempts(self, snapshot: PipelineSnapshot, now: datetime) -> None:
        for attempt in snapshot.attempts:
            self._conn.execute(
                """
                INSERT INTO job_attempts (
                    instance_base_url, project_id, job_id, pipeline_id, exact_name, stage,
                    kind, source_status, observed_failure, is_current_attempt, created_at,
                    started_at, finished_at, gitlab_duration_s, queued_duration_s,
                    allow_failure, runner_id, web_url, failure_reason, first_seen_at,
                    last_seen_at, fetched_at, timestamp_validity, source_presence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (instance_base_url, project_id, job_id) DO UPDATE SET
                    pipeline_id = excluded.pipeline_id,
                    exact_name = excluded.exact_name,
                    stage = excluded.stage,
                    kind = excluded.kind,
                    source_status = excluded.source_status,
                    observed_failure = job_attempts.observed_failure OR excluded.observed_failure,
                    is_current_attempt = excluded.is_current_attempt,
                    created_at = excluded.created_at,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    gitlab_duration_s = excluded.gitlab_duration_s,
                    queued_duration_s = excluded.queued_duration_s,
                    allow_failure = excluded.allow_failure,
                    runner_id = excluded.runner_id,
                    web_url = excluded.web_url,
                    failure_reason = excluded.failure_reason,
                    last_seen_at = excluded.last_seen_at,
                    fetched_at = excluded.fetched_at,
                    timestamp_validity = excluded.timestamp_validity,
                    source_presence = excluded.source_presence
                """,
                [
                    snapshot.instance_base_url,
                    snapshot.project_id,
                    attempt.job_id,
                    attempt.pipeline_id,
                    attempt.exact_name,
                    attempt.stage,
                    attempt.kind.value,
                    attempt.source_status,
                    attempt.observed_failure,
                    attempt.is_current_attempt,
                    _to_naive_utc(attempt.created_at),
                    _to_naive_utc(attempt.started_at),
                    _to_naive_utc(attempt.finished_at),
                    attempt.gitlab_duration_s,
                    attempt.queued_duration_s,
                    attempt.allow_failure,
                    attempt.runner_id,
                    attempt.web_url,
                    attempt.failure_reason,
                    _to_naive_utc(now),
                    _to_naive_utc(now),
                    _to_naive_utc(now),
                    _timestamp_validity(attempt),
                    SourcePresence.PRESENT.value,
                ],
            )

    def _mark_absent(self, snapshot: PipelineSnapshot) -> None:
        if snapshot.completeness is SnapshotCompleteness.INCONSISTENT:
            return
        kinds = (
            (AttemptKind.JOB.value,)
            if snapshot.completeness is SnapshotCompleteness.BRIDGES_UNSUPPORTED
            else (AttemptKind.JOB.value, AttemptKind.BRIDGE.value)
        )
        for kind in kinds:
            present_ids = [
                attempt.job_id for attempt in snapshot.attempts if attempt.kind.value == kind
            ]
            sql = (
                "UPDATE job_attempts SET source_presence = ?, is_current_attempt = FALSE "
                "WHERE instance_base_url = ? AND project_id = ? AND pipeline_id = ? AND kind = ?"
            )
            params: list[Any] = [
                SourcePresence.ABSENT.value,
                snapshot.instance_base_url,
                snapshot.project_id,
                snapshot.pipeline_id,
                kind,
            ]
            if present_ids:
                placeholders = ", ".join("?" for _ in present_ids)
                sql += f" AND job_id NOT IN ({placeholders})"
                params.extend(present_ids)
            self._conn.execute(sql, params)

    def load_snapshots(
        self,
        instance_base_url: str,
        project_id: int,
        start: datetime,
        end: datetime,
        *,
        source: str | None = None,
        ref: str | None = None,
        name: str | None = None,
        limit: int | None = None,
    ) -> list[PipelineSnapshot]:
        conditions = [
            "instance_base_url = ?",
            "project_id = ?",
            "created_at >= ?",
            "created_at < ?",
        ]
        params: list[Any] = [
            instance_base_url,
            project_id,
            _to_naive_utc(start),
            _to_naive_utc(end),
        ]
        for column, value in (("source", source), ("ref", ref), ("name", name)):
            if value is not None:
                conditions.append(f"{column} = ?")
                params.append(value)
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                pipeline_sql = (
                    "SELECT * FROM pipelines WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY created_at "
                    + ("DESC" if limit is not None else "ASC")
                    + ", pipeline_id "
                    + ("DESC" if limit is not None else "ASC")
                )
                if limit is not None:
                    pipeline_sql += " LIMIT ?"
                    pipeline_params = [*params, limit]
                else:
                    pipeline_params = params
                pipeline_rows = self._fetch_rows(pipeline_sql, pipeline_params)
                attempt_rows: list[dict[str, Any]] = []
                if pipeline_rows:
                    pipeline_ids = [int(row["pipeline_id"]) for row in pipeline_rows]
                    placeholders = ", ".join("?" for _ in pipeline_ids)
                    attempt_rows = self._fetch_rows(
                        "SELECT * FROM job_attempts "
                        "WHERE instance_base_url = ? AND project_id = ? "
                        f"AND pipeline_id IN ({placeholders}) "
                        "ORDER BY pipeline_id, job_id",
                        [instance_base_url, project_id, *pipeline_ids],
                    )
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
        if not pipeline_rows:
            return []
        attempts_by_pipeline: dict[int, list[JobAttempt]] = {}
        for row in attempt_rows:
            attempts_by_pipeline.setdefault(int(row["pipeline_id"]), []).append(
                self._row_to_attempt(row)
            )
        return [
            self._row_to_snapshot(row, tuple(attempts_by_pipeline.get(int(row["pipeline_id"]), ())))
            for row in pipeline_rows
        ]

    def recent_snapshots(
        self, instance_base_url: str, project_id: int, limit: int = 50
    ) -> list[PipelineSnapshot]:
        return self.load_snapshots(
            instance_base_url,
            project_id,
            datetime(1970, 1, 1, tzinfo=UTC),
            datetime(2100, 1, 1, tzinfo=UTC),
            limit=limit,
        )

    def coverage(self, instance_base_url: str, project_id: int) -> Coverage:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    count(*),
                    min(p.created_at),
                    max(p.created_at),
                    max(p.fetched_at),
                    sum(
                        CASE
                            WHEN p.snapshot_completeness <> 'complete'
                                OR (p.last_attempt_state IS NOT NULL
                                    AND p.last_attempt_state <> 'complete')
                                OR EXISTS (
                                    SELECT 1 FROM job_attempts a
                                    WHERE a.instance_base_url = p.instance_base_url
                                      AND a.project_id = p.project_id
                                      AND a.pipeline_id = p.pipeline_id
                                      AND a.source_presence = 'absent'
                                )
                            THEN 1 ELSE 0
                        END
                    )
                FROM pipelines p
                WHERE p.instance_base_url = ? AND p.project_id = ?
                """,
                [instance_base_url, project_id],
            ).fetchone()
            last_success = self._conn.execute(
                "SELECT max(finished_at) FROM sync_runs "
                "WHERE instance_base_url = ? AND project_id = ? AND state = 'complete'",
                [instance_base_url, project_id],
            ).fetchone()
        assert row is not None
        return Coverage(
            pipeline_count=int(row[0] or 0),
            first_created_at=_from_naive_utc(row[1]),
            last_created_at=_from_naive_utc(row[2]),
            last_fetched_at=_from_naive_utc(row[3]),
            last_successful_sync_at=_from_naive_utc(last_success[0] if last_success else None),
            incomplete_pipelines=int(row[4] or 0),
        )

    def record_sync_start(
        self,
        instance_base_url: str,
        project_id: int,
        requested_start: datetime,
        requested_end: datetime,
        timezone_name: str,
        requested_start_date: date,
        requested_end_date: date,
        started_at: datetime | None = None,
    ) -> str:
        sync_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sync_runs (
                    sync_id, instance_base_url, project_id, requested_start, requested_end,
                    requested_timezone, requested_start_date, requested_end_date,
                    started_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
                """,
                [
                    sync_id,
                    instance_base_url,
                    project_id,
                    _to_naive_utc(requested_start),
                    _to_naive_utc(requested_end),
                    timezone_name,
                    requested_start_date,
                    requested_end_date,
                    _to_naive_utc(started_at or _now()),
                ],
            )
        return sync_id

    def finish_sync(
        self,
        sync_id: str,
        state: str,
        discovered_count: int,
        completed_count: int,
        errors: Sequence[str],
        finished_at: datetime | None = None,
    ) -> None:
        error_summary = "\n".join(errors[:50]) if errors else None
        with self._lock:
            self._conn.execute(
                "UPDATE sync_runs SET finished_at = ?, state = ?, discovered_count = ?, "
                "completed_count = ?, error_summary = ? WHERE sync_id = ?",
                [
                    _to_naive_utc(finished_at or _now()),
                    state,
                    discovered_count,
                    completed_count,
                    error_summary,
                    sync_id,
                ],
            )

    def mark_interrupted_syncs(self) -> int:
        with self._lock:
            before = self._conn.execute(
                "SELECT count(*) FROM sync_runs WHERE state = 'running'"
            ).fetchone()
            self._conn.execute(
                "UPDATE sync_runs SET state = 'interrupted', finished_at = ? "
                "WHERE state = 'running'",
                [_to_naive_utc(_now())],
            )
        assert before is not None
        return int(before[0] or 0)

    def sync_runs(self, instance_base_url: str, project_id: int, limit: int = 10) -> list[SyncRun]:
        with self._lock:
            rows = self._fetch_rows(
                "SELECT * FROM sync_runs WHERE instance_base_url = ? AND project_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                [instance_base_url, project_id, limit],
            )
        return [self._row_to_sync_run(row) for row in rows]

    def load_pipeline(
        self, instance_base_url: str, project_id: int, pipeline_id: int
    ) -> PipelineSnapshot | None:
        with self._lock:
            rows = self._fetch_rows(
                "SELECT * FROM pipelines "
                "WHERE instance_base_url = ? AND project_id = ? AND pipeline_id = ?",
                [instance_base_url, project_id, pipeline_id],
            )
            if not rows:
                return None
            attempt_rows = self._fetch_rows(
                "SELECT * FROM job_attempts "
                "WHERE instance_base_url = ? AND project_id = ? AND pipeline_id = ? "
                "ORDER BY job_id",
                [instance_base_url, project_id, pipeline_id],
            )
        attempts = tuple(self._row_to_attempt(row) for row in attempt_rows)
        return self._row_to_snapshot(rows[0], attempts)

    def save_analysis(self, name: str, config: SavedAnalysisConfig) -> SavedAnalysis:
        validated = validate_analysis_name(name)
        payload = dump_config(config)
        config = config_from_json(payload)
        analysis_id = uuid.uuid4().hex
        now = _now()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO saved_analyses ("
                    "analysis_id, name, instance_base_url, project_id, config_json, "
                    "config_version, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        analysis_id,
                        validated,
                        config.project.instance_base_url,
                        config.project.project_id,
                        payload,
                        CONFIG_VERSION,
                        _to_naive_utc(now),
                        _to_naive_utc(now),
                    ],
                )
            except duckdb.ConstraintException as exc:
                raise SavedAnalysisNameError(
                    f"a saved comparison named {validated!r} already exists for this project"
                ) from exc
        return SavedAnalysis(
            analysis_id=analysis_id,
            name=validated,
            instance_base_url=config.project.instance_base_url,
            project_id=config.project.project_id,
            config=config,
            created_at=now,
            updated_at=now,
        )

    def update_analysis(
        self,
        analysis_id: str,
        *,
        name: str | None = None,
        config: SavedAnalysisConfig | None = None,
    ) -> SavedAnalysis:
        existing = self.analysis(analysis_id)
        if existing is None:
            raise SavedAnalysisNotFoundError(f"saved analysis not found: {analysis_id}")
        validated = validate_analysis_name(name) if name is not None else existing.name
        updated_config = config if config is not None else existing.config
        payload = dump_config(updated_config)
        updated_config = config_from_json(payload)
        now = _now()
        with self._lock:
            try:
                updated = self._conn.execute(
                    "UPDATE saved_analyses SET name = ?, instance_base_url = ?, project_id = ?, "
                    "config_json = ?, config_version = ?, updated_at = ? "
                    "WHERE analysis_id = ? RETURNING analysis_id",
                    [
                        validated,
                        updated_config.project.instance_base_url,
                        updated_config.project.project_id,
                        payload,
                        CONFIG_VERSION,
                        _to_naive_utc(now),
                        analysis_id,
                    ],
                ).fetchone()
            except duckdb.ConstraintException as exc:
                raise SavedAnalysisNameError(
                    f"a saved comparison named {validated!r} already exists for this project"
                ) from exc
            if updated is None:
                raise SavedAnalysisNotFoundError(f"saved analysis not found: {analysis_id}")
        return SavedAnalysis(
            analysis_id=analysis_id,
            name=validated,
            instance_base_url=updated_config.project.instance_base_url,
            project_id=updated_config.project.project_id,
            config=updated_config,
            created_at=existing.created_at,
            updated_at=now,
        )

    def rename_analysis(self, analysis_id: str, name: str) -> SavedAnalysis:
        return self.update_analysis(analysis_id, name=name)

    def delete_analysis(self, analysis_id: str) -> None:
        with self._lock:
            deleted = self._conn.execute(
                "DELETE FROM saved_analyses WHERE analysis_id = ? RETURNING analysis_id",
                [analysis_id],
            ).fetchall()
        if not deleted:
            raise SavedAnalysisNotFoundError(f"saved analysis not found: {analysis_id}")

    def analysis(self, analysis_id: str) -> SavedAnalysis | None:
        with self._lock:
            rows = self._fetch_rows(
                "SELECT * FROM saved_analyses WHERE analysis_id = ?", [analysis_id]
            )
        if not rows:
            return None
        return self._row_to_saved_analysis(rows[0])

    def analyses(self) -> list[SavedAnalysis]:
        with self._lock:
            rows = self._fetch_rows(
                "SELECT * FROM saved_analyses ORDER BY updated_at DESC, name", []
            )
        return [self._row_to_saved_analysis(row) for row in rows]

    def _row_to_saved_analysis(self, row: dict[str, Any]) -> SavedAnalysis:
        try:
            config = config_from_json(row["config_json"])
        except ConfigValidationError as exc:
            raise StorageError(
                f"saved analysis {row['analysis_id']} has an invalid configuration: {exc}"
            ) from exc
        if int(row["config_version"]) != CONFIG_VERSION:
            raise StorageError(
                f"saved analysis {row['analysis_id']} has unsupported config version "
                f"{row['config_version']}"
            )
        return SavedAnalysis(
            analysis_id=row["analysis_id"],
            name=row["name"],
            instance_base_url=row["instance_base_url"],
            project_id=int(row["project_id"]),
            config=config,
            created_at=_required_from_naive_utc(row["created_at"]),
            updated_at=_required_from_naive_utc(row["updated_at"]),
        )

    def _fetch_rows(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        cursor = self._conn.execute(sql, list(params))
        names = [description[0] for description in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def _row_to_snapshot(
        self, row: dict[str, Any], attempts: tuple[JobAttempt, ...]
    ) -> PipelineSnapshot:
        return PipelineSnapshot(
            instance_base_url=row["instance_base_url"],
            project_id=int(row["project_id"]),
            pipeline_id=int(row["pipeline_id"]),
            iid=int(row["iid"]) if row["iid"] is not None else None,
            name=row["name"],
            ref=row["ref"],
            source=row["source"],
            sha=row["sha"],
            source_status=row["source_status"],
            created_at=_required_from_naive_utc(row["created_at"]),
            started_at=_from_naive_utc(row["started_at"]),
            finished_at=_from_naive_utc(row["finished_at"]),
            updated_at=_from_naive_utc(row["updated_at"]),
            gitlab_duration_s=row["gitlab_duration_s"],
            web_url=row["web_url"],
            attempts=attempts,
            completeness=SnapshotCompleteness(row["snapshot_completeness"]),
            snapshot_error=row["snapshot_error"],
            last_attempt_state=row["last_attempt_state"],
            last_error=row["last_error"],
        )

    def _row_to_attempt(self, row: dict[str, Any]) -> JobAttempt:
        return JobAttempt(
            job_id=int(row["job_id"]),
            pipeline_id=int(row["pipeline_id"]),
            exact_name=row["exact_name"],
            stage=row["stage"],
            kind=AttemptKind(row["kind"]),
            source_status=row["source_status"],
            allow_failure=bool(row["allow_failure"]),
            observed_failure=bool(row["observed_failure"]),
            is_current_attempt=row["is_current_attempt"],
            created_at=_from_naive_utc(row["created_at"]),
            started_at=_from_naive_utc(row["started_at"]),
            finished_at=_from_naive_utc(row["finished_at"]),
            gitlab_duration_s=row["gitlab_duration_s"],
            queued_duration_s=row["queued_duration_s"],
            runner_id=int(row["runner_id"]) if row["runner_id"] is not None else None,
            web_url=row["web_url"],
            failure_reason=row["failure_reason"],
            source_presence=SourcePresence(row["source_presence"]),
        )

    def _row_to_sync_run(self, row: dict[str, Any]) -> SyncRun:
        return SyncRun(
            sync_id=row["sync_id"],
            instance_base_url=row["instance_base_url"],
            project_id=int(row["project_id"]),
            requested_start=_required_from_naive_utc(row["requested_start"]),
            requested_end=_required_from_naive_utc(row["requested_end"]),
            requested_timezone=row["requested_timezone"],
            requested_start_date=row["requested_start_date"],
            requested_end_date=row["requested_end_date"],
            started_at=_required_from_naive_utc(row["started_at"]),
            finished_at=_from_naive_utc(row["finished_at"]),
            state=row["state"],
            discovered_count=int(row["discovered_count"]),
            completed_count=int(row["completed_count"]),
            error_summary=row["error_summary"],
        )
