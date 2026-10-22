from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from glabviz.analysis_config import (
    ChartText,
    ComparisonConfig,
    ExportConfig,
    GanttConfig,
    PeriodConfig,
    PipelineFilters,
    ProjectRef,
    SavedAnalysisConfig,
)
from glabviz.storage import (
    SCHEMA_VERSION,
    SavedAnalysisNameError,
    SavedAnalysisNotFoundError,
    Storage,
    StorageError,
)
from tests.factories import attempt, snapshot

INSTANCE = "https://gitlab.example.com"


def _config(project_id: int = 1) -> SavedAnalysisConfig:
    return SavedAnalysisConfig(
        project=ProjectRef(INSTANCE, project_id),
        filters=PipelineFilters(source="merge_request_event"),
        before=PeriodConfig(date(2026, 5, 1), date(2026, 5, 31), "UTC"),
        after=PeriodConfig(date(2026, 10, 1), date(2026, 10, 18), "UTC"),
        comparison=ComparisonConfig(level="pipeline", integration_prefix="test integration"),
        export=ExportConfig(chart_texts={"failure": ChartText(title="Failures")}),
        gantt=GanttConfig(primary_pipeline_id=1),
    )


@pytest.fixture
def storage(tmp_path):
    store = Storage.open(tmp_path / "data")
    yield store
    store.close()


def test_saved_analysis_crud_roundtrip(storage):
    created = storage.save_analysis("May vs October", _config())
    assert created.analysis_id
    assert created.created_at == created.updated_at

    loaded = storage.analysis(created.analysis_id)
    assert loaded is not None
    assert loaded.name == "May vs October"
    assert loaded.config == _config()
    assert loaded.instance_base_url == INSTANCE
    assert loaded.project_id == 1

    updated = storage.update_analysis(
        created.analysis_id,
        config=replace(_config(), gantt=GanttConfig(primary_pipeline_id=2)),
    )
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at
    assert updated.config.gantt.primary_pipeline_id == 2

    renamed = storage.rename_analysis(created.analysis_id, "Renamed")
    assert renamed.name == "Renamed"
    reloaded = storage.analysis(created.analysis_id)
    assert reloaded is not None and reloaded.name == "Renamed"

    storage.delete_analysis(created.analysis_id)
    assert storage.analysis(created.analysis_id) is None
    with pytest.raises(SavedAnalysisNotFoundError):
        storage.delete_analysis(created.analysis_id)


def test_names_are_unique_per_project(storage):
    storage.save_analysis("Same", _config(project_id=1))
    with pytest.raises(SavedAnalysisNameError):
        storage.save_analysis("Same", _config(project_id=1))
    storage.save_analysis("Same", _config(project_id=2))


def test_saved_crud_does_not_change_data_revision(storage):
    before = storage.data_revision()
    created = storage.save_analysis("Stable", _config())
    storage.delete_analysis(created.analysis_id)
    assert storage.data_revision() == before


def test_corrupt_config_json_is_reported(storage):
    created = storage.save_analysis("Broken", _config())
    storage._conn.execute(  # type: ignore[attr-defined]
        "UPDATE saved_analyses SET config_json = ? WHERE analysis_id = ?",
        ["{", created.analysis_id],
    )
    with pytest.raises(StorageError, match="invalid configuration"):
        storage.analysis(created.analysis_id)


def test_load_pipeline_reads_attempts(storage):
    storage.publish_snapshot(snapshot(7, attempts=(attempt(job_id=70, pipeline_id=7),)))
    loaded = storage.load_pipeline(INSTANCE, 1, 7)
    assert loaded is not None
    assert loaded.pipeline_id == 7
    assert [item.job_id for item in loaded.attempts] == [70]
    assert storage.load_pipeline(INSTANCE, 1, 999) is None


def test_v1_database_is_backed_up_and_migrated(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    migration = (
        Path(__file__).resolve().parents[2] / "src" / "glabviz" / "migrations" / "001_initial.sql"
    ).read_text(encoding="utf-8")
    connection = duckdb.connect(str(directory / "glabviz.duckdb"))
    connection.execute(migration)
    connection.execute(
        "INSERT INTO app_meta (key, value) VALUES ('schema_version', '1'), ('data_revision', '0')"
    )
    connection.execute("INSERT INTO app_meta (key, value) VALUES ('sentinel', 'kept')")
    connection.close()

    store = Storage.open(directory)
    try:
        row = store._conn.execute(  # type: ignore[attr-defined]
            "SELECT value FROM app_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None and int(row[0]) == SCHEMA_VERSION
        sentinel = store._conn.execute(  # type: ignore[attr-defined]
            "SELECT value FROM app_meta WHERE key = 'sentinel'"
        ).fetchone()
        assert sentinel == ("kept",)
        tables = {
            item[0]
            for item in store._conn.execute(  # type: ignore[attr-defined]
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        assert "saved_analyses" in tables
    finally:
        store.close()
    backup = next((directory / "backups").glob("*.duckdb"))
    connection = duckdb.connect(str(backup), read_only=True)
    try:
        version = connection.execute(
            "SELECT value FROM app_meta WHERE key = 'schema_version'"
        ).fetchone()
        sentinel = connection.execute(
            "SELECT value FROM app_meta WHERE key = 'sentinel'"
        ).fetchone()
    finally:
        connection.close()
    assert version == ("1",)
    assert sentinel == ("kept",)


def test_update_analysis_rejects_duplicate_name_and_missing_row(tmp_path):
    store = Storage.open(tmp_path / "data")
    try:
        store.save_analysis("First", _config())
        second = store.save_analysis("Second", _config())
        with pytest.raises(SavedAnalysisNameError):
            store.update_analysis(second.analysis_id, name="First")
        with pytest.raises(SavedAnalysisNotFoundError):
            store.update_analysis("missing", name="Other")
    finally:
        store.close()


def test_saved_analysis_survives_reopen(tmp_path):
    directory = tmp_path / "data"
    first = Storage.open(directory)
    created = first.save_analysis("Persisted", _config())
    first.close()

    second = Storage.open(directory)
    try:
        loaded = second.analysis(created.analysis_id)
        assert loaded is not None
        assert loaded.name == "Persisted"
        assert loaded.config == _config()
    finally:
        second.close()
