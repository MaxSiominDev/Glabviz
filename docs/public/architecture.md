# Architecture

Glabviz runs as a single Python process with a Streamlit UI and a local DuckDB file.
The loopback Streamlit process is the only server component. There is no background daemon
or external service for reading stored data.

## Modules

```text
src/glabviz/
  cli.py               launcher for the Streamlit app
  app.py               page navigation and the shared runtime
  config.py            data directory, GitLab URL/token/CA settings, logging
  domain.py            snapshots, attempts, periods, categories
  gitlab_client.py     REST adapter, pagination, capability degradation
  collector.py         sync orchestration, normalization, snapshot publishing
  storage.py           DuckDB schema, migrations, queries, saved analyses
  metrics.py           classification, wall-clock, pending, failure rates
  comparisons.py       deltas, full outer joins, bucketing
  analysis.py          comparison report orchestration
  analysis_config.py   versioned saved-comparison JSON
  charts.py            pure Plotly figure builders
  gantt.py             Gantt row model and figure builders
  formatting.py        human-readable durations, rates and labels
  migrations/          SQL migrations shipped with the package
  ui/                  Streamlit pages and widgets
```

`domain`, `metrics`, `comparisons`, `analysis`, `analysis_config`, `charts`, `gantt` and
`formatting` never import Streamlit. Chart builders take computed values and return a
Plotly figure without accessing GitLab or the database. The UI uses the existing metric
implementations rather than defining its own.

## Data flow

```text
GitLab REST -> gitlab_client -> collector -> PipelineSnapshot
PipelineSnapshot -> storage.publish_snapshot -> DuckDB (pipeline + attempts)
DuckDB -> storage.load_snapshots -> metrics.* -> analysis.build_report
analysis report -> charts / gantt -> Plotly figure -> Streamlit or browser export
```

A sync lists pipelines for the requested range, fetches the details and every job and
bridge attempt for each pipeline, validates the snapshot, and publishes it in a short
transaction. If a pipeline fails to sync, the error is recorded and the run is marked
`partial`. The remaining pipelines are still stored. Re-running a range is idempotent
by job ID, and a later retry does not erase a previously observed failure.

## Storage

The default directory is `~/.glabviz/`. It contains `glabviz.duckdb`, `logs/` and
`backups/`. The schema version is stored in `app_meta`. Before a migration changes an
older database, it creates a backup. A process-wide file lock prevents a second app
instance from opening the same directory as another writer.

The tables are `app_meta`, `projects`, `pipelines`, `job_attempts`, `sync_runs` and
`saved_analyses`. The first five hold observations and collection state.
`saved_analyses` holds a name and a versioned JSON configuration, never computed numbers.

## Threading and multiple tabs

The Streamlit runtime is cached per data directory and shared across sessions. A single
runtime lock serializes syncs, so two tabs cannot start conflicting collectors. All
database calls use one connection guarded by an `RLock`. Snapshot fetches run in a thread
pool, while all writes stay on the collecting session.

## Offline behavior

Rendering a page, changing a filter, building a chart and opening a saved comparison
never call GitLab. The app can open an existing database without a token. Network access
happens only when the user presses a connection or sync button. Snapshot loads are cached
in memory, with the database revision as the cache key, so a completed sync invalidates
the cache.

## Saved comparisons

Each configuration is JSON with an explicit version. It includes the project, filters,
periods and timezone, level and selected object, dynamics metric and granularity, export
texts and sizes, Gantt pipeline IDs, hidden rows, sorting and an explicit range. Every
field is validated on load. Unknown or future versions produce an error instead of
restoring only part of the configuration. Numbers are always recalculated from the
current database.

## Exports

Charts are Plotly figures rendered by Streamlit. The camera icon in each figure toolbar
downloads a PNG or SVG through the browser. Export options control the format, file name,
title, axis labels, width, height, font size and PNG scale. Hidden rows are filtered out
before the figure is built, and an annotation shows how many rows were hidden. There is
no server-side image renderer, Kaleido requirement or upload to Plotly Cloud. The
cloud-share modebar button is removed.

## Installer layout

`scripts/install.sh` copies a locked source tree into
`~/.local/share/glabviz/versions/<version>-<timestamp>/`, creates a virtual
environment there with `uv sync --locked --no-dev`, runs a package/migrations check, and
then atomically switches the `current` symlink. The launcher in `~/.local/bin` calls
`current/.venv/bin/glabviz`. If installation fails, only the new version directory
is removed, and the previous `current` keeps working. User data in `~/.glabviz` is
never touched.