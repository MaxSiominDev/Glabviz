from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from glabviz.domain import (
    ObjectCategory,
    ProjectInfo,
    SnapshotCompleteness,
    SourcePresence,
)
from glabviz.metrics import classify_pipeline, executed_attempts
from glabviz.storage import Storage
from tests.factories import BASE, attempt, snapshot

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2027, 1, 1, tzinfo=UTC)
INSTANCE = "https://gitlab.example.com"


@pytest.fixture
def storage(tmp_path):
    store = Storage.open(tmp_path / "data")
    yield store
    store.close()


def _load(store: Storage, **kwargs):
    return store.load_snapshots(INSTANCE, 1, START, END, **kwargs)


def _project() -> ProjectInfo:
    return ProjectInfo(project_id=1, path="group/project", name="Project", default_branch="main")


def test_repeated_publish_is_idempotent(storage):
    snap = snapshot(1, attempts=(attempt(job_id=1),))
    storage.publish_snapshot(snap)
    revision = storage.data_revision()
    storage.publish_snapshot(snap)
    loaded = _load(storage)
    assert len(loaded) == 1
    assert len(loaded[0].attempts) == 1
    assert storage.data_revision() == revision + 1


def test_stale_metadata_updates_bump_the_data_revision(storage):
    complete = snapshot(1, attempts=(attempt(job_id=1),))
    storage.publish_snapshot(complete)
    revision = storage.data_revision()
    inconsistent = replace(
        complete,
        completeness=SnapshotCompleteness.INCONSISTENT,
        snapshot_error="refresh failed",
    )
    storage.publish_snapshot(inconsistent)
    assert storage.data_revision() == revision + 1
    assert _load(storage)[0].is_stale

    revision = storage.data_revision()
    storage.record_pipeline_error(INSTANCE, 1, 1, "listing failed")
    assert storage.data_revision() == revision + 1
    loaded = _load(storage)[0]
    assert loaded.is_stale
    assert loaded.last_error == "listing failed"


def test_retry_attempt_updates_pipeline_and_preserves_old_failure(storage):
    storage.publish_snapshot(snapshot(1, attempts=(attempt(job_id=1, is_current=True),)))
    retry = attempt(
        job_id=2,
        status="failed",
        created=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        is_current=True,
    )
    storage.publish_snapshot(
        snapshot(
            1,
            attempts=(
                attempt(job_id=1, is_current=False),
                retry,
            ),
        )
    )
    loaded = _load(storage)[0]
    assert loaded.created_at.month == 5
    assert {attempt.job_id for attempt in loaded.attempts} == {1, 2}
    assert classify_pipeline(loaded, None) is ObjectCategory.RED


def test_observed_failure_survives_attempt_disappearing(storage):
    storage.publish_snapshot(snapshot(1, attempts=(attempt(job_id=1, status="failed"),)))
    storage.publish_snapshot(snapshot(1, attempts=()))
    loaded = _load(storage)[0]
    assert len(loaded.attempts) == 1
    absent = loaded.attempts[0]
    assert absent.source_presence is SourcePresence.ABSENT
    assert absent.observed_failure is True
    assert loaded.is_trustworthy is False
    assert executed_attempts(loaded.attempts) == ()
    assert classify_pipeline(loaded, None) is ObjectCategory.RED
    assert storage.coverage(INSTANCE, 1).incomplete_pipelines == 1


def test_same_ids_on_different_hosts_are_isolated(storage):
    storage.publish_snapshot(
        snapshot(
            1,
            attempts=(attempt(job_id=1),),
            instance_base_url="https://a.example.com",
        )
    )
    storage.publish_snapshot(
        snapshot(
            1,
            attempts=(attempt(job_id=1),),
            instance_base_url="https://b.example.com",
        )
    )
    first = storage.load_snapshots("https://a.example.com", 1, START, END)
    second = storage.load_snapshots("https://b.example.com", 1, START, END)
    assert len(first) == 1
    assert len(second) == 1


def test_special_characters_roundtrip_without_sql_execution(storage):
    name = "x'; DROP TABLE job_attempts; -- <script>alert(1)</script>"
    storage.publish_snapshot(snapshot(1, attempts=(attempt(job_id=1, name=name),)))
    loaded = _load(storage)
    assert loaded[0].attempts[0].exact_name == name


def test_data_survives_reopen(tmp_path):
    directory = tmp_path / "data"
    store = Storage.open(directory)
    store.upsert_project(_project(), INSTANCE, "UTC", {"source": "merge_request_event"})
    store.publish_snapshot(snapshot(1, attempts=(attempt(job_id=1),)))
    store.close()

    reopened = Storage.open(directory)
    try:
        assert len(_load(reopened)) == 1
        assert reopened.projects()[0].local_filter == {"source": "merge_request_event"}
    finally:
        reopened.close()


def test_last_good_snapshot_is_not_overwritten_by_inconsistent(storage):
    storage.publish_snapshot(snapshot(1, attempts=(attempt(job_id=1),)))
    before = storage.coverage(INSTANCE, 1).last_fetched_at
    storage.publish_snapshot(
        snapshot(
            1,
            attempts=(attempt(job_id=1),),
            completeness=SnapshotCompleteness.INCONSISTENT,
            snapshot_error="changed during fetch",
        )
    )
    loaded = _load(storage)[0]
    assert loaded.completeness is SnapshotCompleteness.COMPLETE
    assert loaded.last_attempt_state == "inconsistent"
    assert loaded.last_error == "changed during fetch"
    assert storage.coverage(INSTANCE, 1).last_fetched_at == before


def test_coverage_and_sync_runs_are_persisted(storage):
    storage.upsert_project(_project(), INSTANCE, "UTC", {})
    sync_id = storage.record_sync_start(
        INSTANCE,
        1,
        datetime(2026, 5, 1, tzinfo=UTC),
        datetime(2026, 6, 1, tzinfo=UTC),
        "UTC",
        date(2026, 5, 1),
        date(2026, 5, 31),
    )
    storage.publish_snapshot(snapshot(1, attempts=(attempt(job_id=1),)))
    storage.finish_sync(sync_id, "complete", 1, 1, [])
    coverage = storage.coverage(INSTANCE, 1)
    assert coverage.pipeline_count == 1
    assert coverage.last_successful_sync_at is not None
    runs = storage.sync_runs(INSTANCE, 1)
    assert runs[0].state == "complete"
    assert runs[0].discovered_count == 1
    assert runs[0].completed_count == 1


def test_running_syncs_can_be_marked_interrupted(storage):
    storage.record_sync_start(
        INSTANCE,
        1,
        START,
        END,
        "UTC",
        date(2026, 5, 1),
        date(2026, 5, 31),
    )
    assert storage.mark_interrupted_syncs() == 1
    assert storage.sync_runs(INSTANCE, 1)[0].state == "interrupted"


def test_filters_apply_to_loaded_snapshots(storage):
    storage.publish_snapshot(
        snapshot(
            1,
            attempts=(attempt(job_id=1),),
            source="merge_request_event",
            ref="refs/merge-requests/1/head",
            name="mr",
        )
    )
    storage.publish_snapshot(
        snapshot(
            2,
            attempts=(attempt(job_id=2),),
            source="push",
            ref="refs/heads/main",
            name="push",
        )
    )
    assert len(_load(storage, source="merge_request_event")) == 1
    assert len(_load(storage, ref="refs/heads/main")) == 1
    assert len(_load(storage, name="mr")) == 1


def test_recent_snapshots_are_limited_and_newest_first(storage):
    for index in range(3):
        storage.publish_snapshot(
            snapshot(
                index + 1,
                created_at=BASE + timedelta(days=index),
                attempts=(attempt(job_id=index + 1),),
            )
        )
    recent = storage.recent_snapshots(INSTANCE, 1, limit=2)
    assert [snap.pipeline_id for snap in recent] == [3, 2]


def test_second_process_cannot_open_the_same_database(tmp_path):
    directory = tmp_path / "data"
    store = Storage.open(directory)
    code = """
import sys
from pathlib import Path
from glabviz.storage import Storage, StorageLockedError
try:
    Storage.open(Path(sys.argv[1]))
except StorageLockedError:
    print("locked")
else:
    print("opened")
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(directory)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        store.close()
    assert result.stdout.strip() == "locked", result.stderr
