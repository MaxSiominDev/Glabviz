from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_docs_are_present():
    for name in ("metrics.md", "architecture.md", "publishing.md"):
        path = ROOT / "docs" / "public" / name
        assert path.is_file(), f"missing public doc: {name}"
        assert len(path.read_text(encoding="utf-8")) > 500


def test_readme_covers_the_user_scenario():
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    for word in ("install", "run", "uninstall", "token", "offline", "placeholder"):
        assert word in text


def test_private_docs_stay_ignored_and_public_docs_are_not():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "docs/*" in text
    assert "!docs/public/" in text


def test_public_files_do_not_contain_a_token_prefix():
    paths = [ROOT / "README.md", *(ROOT / "docs" / "public").glob("*.md")]
    for path in paths:
        assert "glpat-" not in path.read_text(encoding="utf-8")


def test_installer_scripts_have_valid_shell_syntax():
    for name in ("install.sh", "smoke-installed.sh"):
        subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / name)],
            check=True,
            capture_output=True,
        )
