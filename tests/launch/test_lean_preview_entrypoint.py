from __future__ import annotations

import ast
import importlib.util
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "src" / "tianshu" / "lean_preview_demo.py"
WRAPPER_PATH = ROOT / "scripts" / "run_lean_preview_demo.py"


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }


def test_pyproject_registers_installed_lean_demo_entrypoint() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["scripts"]["tianshu-lean-demo"] == "tianshu.lean_preview_demo:main"
    assert MODULE_PATH.is_file()


def test_installed_runner_has_no_private_tianshu_imports() -> None:
    assert _import_roots(MODULE_PATH) <= {
        "__future__",
        "argparse",
        "collections",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "sys",
        "time",
        "typing",
        "urllib",
    }
    spec = importlib.util.spec_from_file_location("installed_lean_preview_demo", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.run_demo)
    assert callable(module.main)


def test_source_tree_script_is_only_a_stdlib_delegating_wrapper() -> None:
    source = WRAPPER_PATH.read_text(encoding="utf-8")

    assert _import_roots(WRAPPER_PATH) <= {"runpy"}
    assert "tianshu.lean_preview_demo" in source
    assert len(source.splitlines()) <= 12
