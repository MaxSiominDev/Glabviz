# Publishing checklist

The project is not published yet. The repository address, the owner and the license are
not chosen. Nothing below should be treated as a live installation URL.

## Before a public release

1. Choose a license and the copyright holder. Do not copy a license from another project
   without that decision.
2. Choose the public repository owner and name.
3. Decide which documentation is public. This directory is the public subset; private
   working notes live elsewhere and stay ignored by git.
4. Check that no real tokens, private host names, project IDs, pipeline IDs, job names or
   API responses from a private installation are present in code, tests, fixtures, docs or
   screenshots.
5. Run the full check suite and the installed-package smoke on the platform you claim to
   support.
6. Build a release tarball and publish a checksum next to it.

## Release archive

The installer accepts `--source`, `--archive FILE` and `--source-url URL`. A release
tarball must contain a top-level directory with `pyproject.toml`, `uv.lock`, `src/` and
`scripts/`:

```text
glabviz-<version>/
  pyproject.toml
  uv.lock
  src/glabviz/...
  scripts/install.sh
  scripts/smoke-installed.sh
  README.md
```

Create it from a clean checkout:

```bash
uv build
tar -czf "glabviz-$(uv run python -c 'import glabviz; print(glabviz.__version__)').tar.gz" \
  pyproject.toml uv.lock src scripts README.md .streamlit
shasum -a 256 glabviz-*.tar.gz
```

The archive must not contain a `.venv`, the database, logs, private notes or real API
responses.

## Published install command

Once a real location exists, the one-liner is:

```bash
curl -fsSL '<install.sh URL>' | bash -s -- --source-url '<release tarball URL>' --sha256 '<checksum>'
```

Until then this is a template. Do not announce, link or test-claim a public installation
that has not been published and verified.

The installer supports Apple Silicon and Intel macOS where the chosen Python and wheels
are available. A check performed on one architecture does not prove the other. Linux is
not a supported installation target for this installer.

## What was verified locally

The local form `bash scripts/install.sh --source .` was run in a clean HOME whose path
contained spaces. The following were checked: a versioned environment, the `current`
symlink switch, the launcher outside the checkout, `--version` and `--help`, the storage
and package smoke, a real server health check, a second install preserving user data, and
refusal to overwrite a foreign executable without `--force`. Browser checks covered PNG
and SVG downloads, hidden rows, per-chart titles and two-panel Gantt exports.

## Uninstall and data

Removing the application:

```bash
rm -rf ~/.local/share/glabviz
rm -f ~/.local/bin/glabviz
```

The data directory `~/.glabviz` is intentionally not removed. There is no command that
deletes user history automatically.
