# Glabviz

Local retrospective analytics for GitLab CI/CD. It stores pipeline history in DuckDB and
shows failure rates, wall-clock time, queue waits, period comparisons, per-pipeline Gantt
charts and PNG/SVG exports. It runs on your machine, reads GitLab through the REST API and
opens without a network connection once data is loaded.

## Requirements

- macOS on Apple Silicon or Intel.
- [uv](https://docs.astral.sh/uv/), which the installer downloads if it is missing.
- Network access for the first install. Python 3.14 is installed by uv if needed.
- A GitLab personal access token with read access to the project (`read_api` is enough).

## Install

From a checkout:

```bash
bash scripts/install.sh --source .
```

The installer creates a versioned environment under `~/.local/share/glabviz/`, writes
a launcher to `~/.local/bin/glabviz` and switches to the new version only after a smoke
check. It never touches `~/.glabviz`, where your data lives.

A published one-liner has this shape:

```bash
curl -fsSL '<install.sh URL>' | bash -s -- --source-url '<release tarball URL>'
```

The URLs are placeholders until a public repository and release location are chosen.
Nothing in this repository claims that a published installer exists today. See
[docs/public/publishing.md](docs/public/publishing.md).

Other installer options: `--archive FILE`, `--source-url URL`, `--sha256 HEX`,
`--prefix DIR`, `--bin-dir DIR`, `--python VERSION`, `--force`. `--sha256` verifies an
archive downloaded with `--source-url`; it is rejected for other sources. `--force` is only
needed to replace a foreign executable named `glabviz`. If `~/.local/bin` is not in
`PATH`, the installer prints the exact export line.

## Run

```bash
glabviz
```

Options: `--help`, `--version`, `--data-dir DIR`, `--port PORT`, `--no-browser`. The app
binds `127.0.0.1` only. Stop it with Ctrl+C. Data and saved comparisons survive restarts.

## GitLab connection

Open the **Data** page and fill in the GitLab base URL, the project ID or path, a personal
access token and a date range. `Test connection` checks the project; `Load / update`
re-reads the chosen range and stores it locally.

Environment variables:

| Variable | Meaning |
|---|---|
| `GLABVIZ_GITLAB_URL` | default GitLab base URL |
| `GLABVIZ_GITLAB_TOKEN` | token shown in the password field |
| `GLABVIZ_CA_BUNDLE` or `REQUESTS_CA_BUNDLE` | corporate CA bundle for TLS |
| `GLABVIZ_DATA_DIR` | data directory override |

The token is never written to the database. TLS verification stays on; there is no
`verify=False` path.

## Data directory

The default is `~/.glabviz/`:

```text
glabviz.duckdb      local database
logs/          application log
backups/       copy taken before a schema migration
```

Use `--data-dir` or `GLABVIZ_DATA_DIR` to work with a separate history. Removing the
installed application does not remove this directory.

## Workflow

1. **Data**: load a project and a date range. Later views work offline.
2. **Compare**: pick periods A and B, filters, timezone and level (pipeline, stage or job).
   Read the cards, failure/duration/pending charts, dynamics and the stage/job table.
   The "What was counted" block explains denominators and exclusions.
3. **Gantt**: choose one or two stored pipelines, search and hide rows, sort, set an
   explicit time range and inspect every attempt in the table.
4. **Saved**: open, rename or delete named comparisons. A saved comparison stores settings,
   not a snapshot of the numbers, so it reflects the current database.

Charts download through the camera icon in the Plotly toolbar. Set the format, size, font,
PNG scale and per-chart title, file name and axis labels before exporting. Exports never
contain the app sidebar, tokens or hidden rows.

## Demo data

For an offline tour or a browser check:

```bash
uv run python scripts/seed_demo.py --data-dir "/tmp/glabviz-demo" --pipelines 20
uv run glabviz --data-dir "/tmp/glabviz-demo"
```

The script refuses to reuse an existing database unless `--force` is given.

## Development

```bash
uv sync --locked
uv run glabviz
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv build
bash -n scripts/install.sh scripts/smoke-installed.sh
```

Installed-package smoke:

```bash
bash scripts/smoke-installed.sh --venv ~/.local/share/glabviz/current/.venv --server
```

## Uninstall

Delete the application files, keeping your history:

```bash
rm -rf ~/.local/share/glabviz
rm -f ~/.local/bin/glabviz
```

Delete `~/.glabviz` as well only if you also want to remove the local database.

## Documentation

- [docs/public/metrics.md](docs/public/metrics.md): metric definitions and formulas.
- [docs/public/architecture.md](docs/public/architecture.md): modules and data flow.
