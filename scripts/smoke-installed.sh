#!/usr/bin/env bash
# Smoke-check an installed glabviz environment
set -eu -o pipefail

VENV=""
SERVER=0
DATA_DIR=""
PORT="${GLABVIZ_SMOKE_PORT:-8598}"
TMP_DIR=""
SERVER_PID=""

usage() {
    cat <<'EOF'
Usage: smoke-installed.sh --venv PATH [--server] [--data-dir PATH]

  --venv PATH      virtual environment created by install.sh
  --server         also start the app and check /_stcore/health
  --data-dir PATH  reuse this data directory instead of a temporary one
EOF
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}

trap cleanup EXIT HUP INT TERM

while [ $# -gt 0 ]; do
    case "$1" in
        --venv)
            [ $# -ge 2 ] || die "--venv needs a path"
            VENV="$2"
            shift 2
            ;;
        --server)
            SERVER=1
            shift
            ;;
        --data-dir)
            [ $# -ge 2 ] || die "--data-dir needs a path"
            DATA_DIR="$2"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[ -n "$VENV" ] || die "--venv is required"
CLI="$VENV/bin/glabviz"
PYTHON="$VENV/bin/python"
[ -x "$CLI" ] || die "missing launcher: $CLI"
[ -x "$PYTHON" ] || die "missing python: $PYTHON"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/glabviz-smoke.XXXXXX")"
if [ -z "$DATA_DIR" ]; then
    DATA_DIR="$TMP_DIR/data dir"
    mkdir -p "$DATA_DIR"
fi

"$CLI" --version
"$CLI" --help | grep -q -- '--data-dir' || die "--help does not mention --data-dir"
"$CLI" --help | grep -q -- '--port' || die "--help does not mention --port"
"$CLI" --help | grep -q -- '--no-browser' || die "--help does not mention --no-browser"

"$PYTHON" - "$DATA_DIR" <<'PY'
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

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
from glabviz.collector import normalize_pipeline
from glabviz.domain import SnapshotCompleteness
from glabviz.gitlab_client import RawSnapshot
from glabviz.storage import Storage

data_dir = Path(sys.argv[1])
start = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
detail = {
    "id": 1,
    "iid": 1,
    "project_id": 1,
    "status": "success",
    "source": "merge_request_event",
    "ref": "refs/merge-requests/1/head",
    "name": "pipeline",
    "sha": "sha",
    "created_at": "2026-05-04T09:59:00Z",
    "started_at": "2026-05-04T10:00:00Z",
    "finished_at": "2026-05-04T10:10:00Z",
    "updated_at": "2026-05-04T10:10:00Z",
    "duration": 600,
    "web_url": "https://gitlab.example.com/pipelines/1",
}
job = {
    "id": 10,
    "name": "build",
    "stage": "build",
    "status": "success",
    "allow_failure": False,
    "created_at": "2026-05-04T10:00:00Z",
    "started_at": "2026-05-04T10:00:01Z",
    "finished_at": "2026-05-04T10:05:00Z",
    "queued_duration": 1.0,
    "duration": 299.0,
    "web_url": "https://gitlab.example.com/jobs/10",
}
raw = RawSnapshot(
    instance_base_url="https://gitlab.example.com",
    project_id=1,
    detail=detail,
    jobs=(job,),
    current_job_ids=frozenset({10}),
    bridges=(),
    current_bridge_ids=frozenset(),
    bridges_supported=True,
    completeness=SnapshotCompleteness.COMPLETE,
)
config = SavedAnalysisConfig(
    project=ProjectRef("https://gitlab.example.com", 1),
    filters=PipelineFilters(source="merge_request_event"),
    before=PeriodConfig(date(2026, 5, 1), date(2026, 5, 31), "UTC"),
    after=PeriodConfig(date(2026, 10, 1), date(2026, 10, 18), "UTC"),
    comparison=ComparisonConfig(level="pipeline"),
    export=ExportConfig(chart_texts={"failure": ChartText(title="Smoke")}),
    gantt=GanttConfig(primary_pipeline_id=1),
)
storage = Storage.open(data_dir)
try:
    storage.publish_snapshot(normalize_pipeline(raw))
    saved = storage.save_analysis("smoke", config)
finally:
    storage.close()

storage = Storage.open(data_dir)
try:
    assert storage.analysis(saved.analysis_id) is not None
    loaded = storage.load_pipeline("https://gitlab.example.com", 1, 1)
    assert loaded is not None and loaded.pipeline_id == 1
    assert loaded.attempts[0].job_id == 10
finally:
    storage.close()
print("storage smoke ok")
PY

if [ "$SERVER" -eq 1 ]; then
    "$CLI" --no-browser --port "$PORT" --data-dir "$DATA_DIR" >"$TMP_DIR/server.log" 2>&1 &
    SERVER_PID=$!
    healthy=0
    attempt=0
    while [ "$attempt" -lt 30 ]; do
        attempt=$((attempt + 1))
        sleep 1
        if curl -fsS "http://127.0.0.1:$PORT/_stcore/health" >/dev/null 2>&1; then
            healthy=1
            break
        fi
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            break
        fi
    done
    if [ "$healthy" -ne 1 ]; then
        cat "$TMP_DIR/server.log" >&2 || true
        die "the app did not become healthy on port $PORT (set GLABVIZ_SMOKE_PORT to change it)"
    fi
    kill "$SERVER_PID"
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
    echo "server smoke ok"
fi

echo "installed package smoke ok: $DATA_DIR"
