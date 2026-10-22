#!/usr/bin/env bash
# Install glabviz for the current user on macOS.
#
# Local checkout:      bash scripts/install.sh --source .
# Release archive:     bash scripts/install.sh --archive glabviz-0.1.0.tar.gz
# Published installer: curl -fsSL '<install.sh URL>' | bash -s -- --source-url '<tarball URL>'
set -eu -o pipefail

MARKER="glabviz launcher: managed by scripts/install.sh"
PREFIX="${GLABVIZ_PREFIX:-$HOME/.local/share/glabviz}"
BIN_DIR="${GLABVIZ_BIN_DIR:-$HOME/.local/bin}"
SOURCE=""
SOURCE_URL="${GLABVIZ_SOURCE_URL:-}"
ARCHIVE=""
EXPECTED_SHA=""
FORCE=0
PYTHON_VERSION="3.14"
TMP_DIR=""
RELEASE_DIR=""
CURRENT_TMP=""
LAUNCHER_TMP=""
SWITCHED=0

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Install glabviz for the current macOS user without sudo.

Sources (choose one):
  --source PATH        local checkout, for example --source .
  --archive FILE       release tarball (.tar.gz or .tgz)
  --source-url URL     https URL of the release tarball
  --sha256 HEX         verify the downloaded archive

Install locations:
  --prefix DIR         default: ~/.local/share/glabviz
  --bin-dir DIR        default: ~/.local/bin
  --force              replace a foreign executable named glabviz
  --python VERSION     default: 3.14
  -h, --help           show this help

The installer never touches ~/.glabviz, where your data lives.
EOF
}

log() {
    printf '%s\n' "$*"
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
    if [ -n "$LAUNCHER_TMP" ] && { [ -e "$LAUNCHER_TMP" ] || [ -L "$LAUNCHER_TMP" ]; }; then
        rm -f "$LAUNCHER_TMP"
    fi
    if [ -n "$CURRENT_TMP" ] && { [ -e "$CURRENT_TMP" ] || [ -L "$CURRENT_TMP" ]; }; then
        rm -f "$CURRENT_TMP"
    fi
    if [ "$SWITCHED" -eq 0 ] && [ -n "$RELEASE_DIR" ] && [ -d "$RELEASE_DIR" ] &&
        [ "$(readlink "$PREFIX/current" 2>/dev/null || true)" != "$RELEASE_DIR" ]; then
        rm -rf "$RELEASE_DIR"
    fi
}

trap cleanup EXIT HUP INT TERM

while [ $# -gt 0 ]; do
    case "$1" in
        --source)
            [ $# -ge 2 ] || die "--source needs a path"
            SOURCE="$2"
            shift 2
            ;;
        --source-url | --url)
            [ $# -ge 2 ] || die "--source-url needs a URL"
            SOURCE_URL="$2"
            shift 2
            ;;
        --archive)
            [ $# -ge 2 ] || die "--archive needs a file"
            ARCHIVE="$2"
            shift 2
            ;;
        --sha256)
            [ $# -ge 2 ] || die "--sha256 needs a value"
            EXPECTED_SHA="$2"
            shift 2
            ;;
        --prefix)
            [ $# -ge 2 ] || die "--prefix needs a directory"
            PREFIX="$2"
            shift 2
            ;;
        --bin-dir)
            [ $# -ge 2 ] || die "--bin-dir needs a directory"
            BIN_DIR="$2"
            shift 2
            ;;
        --python)
            [ $# -ge 2 ] || die "--python needs a version"
            PYTHON_VERSION="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1 (use --help)"
            ;;
    esac
done

[ "$(uname -s)" = "Darwin" ] || die "this installer supports macOS only"
case "$(uname -m)" in
    arm64 | x86_64) ;;
    *) die "unsupported architecture: $(uname -m)" ;;
esac

case "$PREFIX" in
    /*) ;;
    *) PREFIX="$(pwd)/$PREFIX" ;;
esac
case "$BIN_DIR" in
    /*) ;;
    *) BIN_DIR="$(pwd)/$BIN_DIR" ;;
esac
if [ -n "$EXPECTED_SHA" ] && [ -z "$SOURCE_URL" ]; then
    die "--sha256 applies only to --source-url"
fi

if [ -z "$SOURCE" ] && [ -z "$SOURCE_URL" ] && [ -z "$ARCHIVE" ]; then
    if [ -f "pyproject.toml" ]; then
        SOURCE="."
    else
        die "no source given; use --source PATH, --archive FILE or --source-url URL"
    fi
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/glabviz.XXXXXX")"
mkdir -p "$TMP_DIR/source"

if [ -n "$SOURCE" ]; then
    [ -d "$SOURCE" ] || die "source directory not found: $SOURCE"
    SOURCE_ROOT="$(cd "$SOURCE" && pwd)"
    log "Copying source from $SOURCE_ROOT"
    (
        cd "$SOURCE_ROOT"
        tar -cf - \
            --exclude=.git \
            --exclude=.venv \
            --exclude=dist \
            --exclude=docs \
            --exclude=__pycache__ \
            --exclude=.mypy_cache \
            --exclude=.pytest_cache \
            --exclude=.ruff_cache \
            --exclude=.idea \
            --exclude=AGENTS.md \
            .
    ) | (cd "$TMP_DIR/source" && tar -xf -)
elif [ -n "$ARCHIVE" ]; then
    [ -f "$ARCHIVE" ] || die "archive not found: $ARCHIVE"
    log "Extracting $ARCHIVE"
    tar -xf "$ARCHIVE" -C "$TMP_DIR/source"
else
    case "$SOURCE_URL" in
        https://*) ;;
        *) die "--source-url must start with https://" ;;
    esac
    log "Downloading $SOURCE_URL"
    curl -fsSL --retry 3 "$SOURCE_URL" -o "$TMP_DIR/source.tar.gz" ||
        die "download failed"
    if [ -n "$EXPECTED_SHA" ]; then
        ACTUAL_SHA="$(shasum -a 256 "$TMP_DIR/source.tar.gz" | awk '{print $1}')"
        [ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] || die "checksum mismatch"
    fi
    tar -xzf "$TMP_DIR/source.tar.gz" -C "$TMP_DIR/source"
fi

if [ -f "$TMP_DIR/source/pyproject.toml" ]; then
    PROJECT="$TMP_DIR/source"
else
    set -- "$TMP_DIR"/source/*/pyproject.toml
    [ -f "$1" ] || die "the source does not contain pyproject.toml"
    PROJECT="$(dirname "$1")"
fi
[ -f "$PROJECT/uv.lock" ] || die "the source does not contain uv.lock"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$PROJECT/src/glabviz/__init__.py" | head -n 1)"
[ -n "$VERSION" ] || die "cannot detect the package version"
case "$VERSION" in
    *[!A-Za-z0-9._-]*) die "unsupported version string: $VERSION" ;;
esac

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

UV="$(find_uv || true)"
if [ -z "$UV" ]; then
    log "uv is not installed. Downloading the official installer from https://astral.sh/uv/install.sh."
    curl -fsSL https://astral.sh/uv/install.sh -o "$TMP_DIR/uv-install.sh" ||
        die "cannot download the uv installer"
    sh "$TMP_DIR/uv-install.sh" || die "uv installation failed"
    UV="$(find_uv || true)"
    [ -n "$UV" ] || die "uv was installed but not found; add $HOME/.local/bin to PATH and rerun"
fi

if ! "$UV" python find "$PYTHON_VERSION" >/dev/null 2>&1; then
    log "Downloading Python $PYTHON_VERSION with uv."
    "$UV" python install "$PYTHON_VERSION" || die "cannot install Python $PYTHON_VERSION"
fi

LAUNCHER="$BIN_DIR/glabviz"
if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
    if ! grep -q "glabviz launcher: managed by" "$LAUNCHER" 2>/dev/null; then
        [ "$FORCE" -eq 1 ] ||
            die "$LAUNCHER exists and was not created by this installer; rerun with --force to replace it"
    fi
fi
if [ -e "$PREFIX/current" ] && [ ! -L "$PREFIX/current" ]; then
    die "$PREFIX/current exists and is not a symlink"
fi

mkdir -p "$PREFIX/versions" "$BIN_DIR"
STAMP="$(date -u +%Y%m%d-%H%M%S)-$$"
RELEASE_DIR="$PREFIX/versions/$VERSION-$STAMP"
mkdir -p "$RELEASE_DIR"
cp -R "$PROJECT/." "$RELEASE_DIR/"

log "Creating the environment in $RELEASE_DIR"
if ! UV_PROJECT_ENVIRONMENT="$RELEASE_DIR/.venv" "$UV" sync \
    --locked --no-dev --no-editable --python "$PYTHON_VERSION" --project "$RELEASE_DIR"; then
    die "dependency installation failed; the previous installation was not changed"
fi

log "Checking the installed package"
if ! "$RELEASE_DIR/.venv/bin/python" - <<'PY'
import importlib.resources

import glabviz
from glabviz.cli import main

assert callable(main)
for name in ("001_initial.sql", "002_saved_analyses.sql"):
    assert (
        importlib.resources.files("glabviz").joinpath("migrations", name).is_file()
    ), name
print(glabviz.__version__)
PY
then
    die "the installed package failed its smoke check; the previous installation was not changed"
fi

LAUNCHER_TMP="$LAUNCHER.tmp.$$"
cat >"$LAUNCHER_TMP" <<EOF
#!/bin/sh
# $MARKER
exec "$PREFIX/current/.venv/bin/glabviz" "\$@"
EOF
chmod 755 "$LAUNCHER_TMP"
mv -f "$LAUNCHER_TMP" "$LAUNCHER"
LAUNCHER_TMP=""

CURRENT_TMP="$PREFIX/current.tmp.$$"
ln -sfn "$RELEASE_DIR" "$CURRENT_TMP"
mv -fh "$CURRENT_TMP" "$PREFIX/current"
CURRENT_TMP=""
SWITCHED=1
[ "$(readlink "$PREFIX/current")" = "$RELEASE_DIR" ] ||
    die "the current symlink did not switch to $RELEASE_DIR"

log "Installed glabviz $VERSION"
log "Launcher: $LAUNCHER"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        log "Add the launcher directory to PATH:"
        log "  export PATH=\"$BIN_DIR:\$PATH\""
        log "Or run it with the full path: $LAUNCHER"
        ;;
esac
log "Your data in ~/.glabviz was not touched."
log "Start the app with: glabviz"
