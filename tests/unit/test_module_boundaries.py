from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "glabviz"
PURE_MODULES = (
    "domain.py",
    "metrics.py",
    "comparisons.py",
    "analysis.py",
    "analysis_config.py",
    "charts.py",
    "gantt.py",
    "formatting.py",
)
FORBIDDEN_PREFIXES = (
    "streamlit",
    "glabviz.storage",
    "glabviz.gitlab_client",
    "glabviz.ui",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def test_pure_modules_do_not_import_ui_or_sources():
    for name in PURE_MODULES:
        for imported in _imports(SOURCE / name):
            for forbidden in FORBIDDEN_PREFIXES:
                assert not imported.startswith(forbidden), f"{name} must not import {imported}"
