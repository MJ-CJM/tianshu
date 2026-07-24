"""Repository-wide guard for the external-process execution boundary."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class LaunchSite:
    path: str
    qualname: str
    resolved_call: str


@dataclass(frozen=True)
class Finding:
    site: LaunchSite
    line: int
    reason: str = "direct process launch"

    def describe(self) -> str:
        return (
            f"{self.site.path}:{self.line}: {self.reason}: "
            f"{self.site.qualname} -> {self.site.resolved_call}"
        )


_SUBPROCESS_LAUNCHERS = {
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "subprocess.run",
}
_ASYNCIO_LAUNCHERS = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
}
_DYNAMIC_IMPORTS = {
    "__import__",
    "builtins.__import__",
    "importlib.import_module",
}
_PROCESS_MODULES = {"asyncio", "os", "subprocess"}


def _is_os_launcher(resolved: str) -> bool:
    if not resolved.startswith("os."):
        return False
    name = resolved.removeprefix("os.")
    return (
        name in {"system", "popen", "fork", "forkpty"}
        or name.startswith("spawn")
        or name.startswith("posix_spawn")
        or name.startswith("exec")
    )


def _is_process_launcher(resolved: str) -> bool:
    return (
        resolved in _SUBPROCESS_LAUNCHERS
        or resolved in _ASYNCIO_LAUNCHERS
        or _is_os_launcher(resolved)
    )


class _ProcessCallScanner(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._scopes: list[dict[str, str]] = [{}]
        self._qualnames: list[str] = []
        self.findings: list[Finding] = []

    @property
    def _qualname(self) -> str:
        return ".".join(self._qualnames) or "<module>"

    def _bind(self, name: str, resolved: str) -> None:
        self._scopes[-1][name] = resolved

    def _resolve_name(self, name: str) -> str:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return name

    def _resolve(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def _add(self, node: ast.AST, resolved: str, reason: str = "direct process launch") -> None:
        self.findings.append(
            Finding(
                site=LaunchSite(self._path, self._qualname, resolved),
                line=getattr(node, "lineno", 0),
                reason=reason,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            resolved = alias.name if alias.asname else bound
            self._bind(bound, resolved)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                if module.split(".", 1)[0] in _PROCESS_MODULES:
                    self._add(node, f"{module}.*", "star import can hide a process launch")
                continue
            bound = alias.asname or alias.name
            self._bind(bound, f"{module}.{alias.name}" if module else alias.name)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        resolved = self._resolve(node.value)
        if resolved is None:
            return
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._bind(target.id, resolved)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is None:
            return
        self.visit(node.value)
        resolved = self._resolve(node.value)
        if resolved is not None and isinstance(node.target, ast.Name):
            self._bind(node.target.id, resolved)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.visit(node.value)
        resolved = self._resolve(node.value)
        if resolved is not None and isinstance(node.target, ast.Name):
            self._bind(node.target.id, resolved)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        resolved = self._resolve(node.func)
        if resolved is not None:
            if _is_process_launcher(resolved):
                self._add(node, resolved)
            elif resolved in _DYNAMIC_IMPORTS:
                module = None
                if node.args and isinstance(node.args[0], ast.Constant):
                    module = node.args[0].value
                if not isinstance(module, str) or module.split(".", 1)[0] in _PROCESS_MODULES:
                    self._add(node, resolved, "dynamic import can hide a process launch")
        self.generic_visit(node)

    def _visit_qualified_body(self, name: str, body: list[ast.stmt]) -> None:
        self._qualnames.append(name)
        self._scopes.append({})
        for statement in body:
            self.visit(statement)
        self._scopes.pop()
        self._qualnames.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        self._visit_qualified_body(node.name, node.body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        self._visit_qualified_body(node.name, node.body)

    visit_AsyncFunctionDef = visit_FunctionDef


def _scan_source(source: str, *, path: str) -> list[Finding]:
    scanner = _ProcessCallScanner(path)
    scanner.visit(ast.parse(source, filename=path))
    return scanner.findings


def _scan_tree(repository_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    paths = set(repository_root.glob("*.py"))
    for source_root in (repository_root / "src" / "tianshu", repository_root / "scripts"):
        if source_root.is_dir():
            paths.update(source_root.rglob("*.py"))
    for path in sorted(paths):
        relative = path.relative_to(repository_root).as_posix()
        findings.extend(_scan_source(path.read_text(encoding="utf-8"), path=relative))
    return findings


def _gate_errors(findings: list[Finding], allowlist: tuple[LaunchSite, ...]) -> list[str]:
    remaining = Counter(allowlist)
    errors: list[str] = []
    for finding in findings:
        if finding.reason == "direct process launch" and remaining[finding.site] > 0:
            remaining[finding.site] -= 1
        else:
            errors.append(finding.describe())
    for site, count in remaining.items():
        errors.extend(
            f"stale process exemption: {site.path}: {site.qualname} -> {site.resolved_call}"
            for _ in range(count)
        )
    return errors


_ALLOWED_LAUNCH_SITES = (
    LaunchSite(
        "src/tianshu/executor/execution_gateway/process_backend.py",
        "AsyncioProcessBackend.spawn",
        "asyncio.create_subprocess_exec",
    ),
    LaunchSite(
        "src/tianshu/executor/git_backend.py",
        "GitBackend._invoke",
        "subprocess.run",
    ),
)


def test_repository_has_only_exact_process_launch_sites() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _scan_tree(root)

    assert _gate_errors(findings, _ALLOWED_LAUNCH_SITES) == []


def test_scanner_resolves_import_aliases_and_simple_assignments() -> None:
    findings = _scan_source(
        """
import subprocess as process
launch = process.run

def outer():
    invoke = launch
    invoke(["tool"])
""",
        path="src/tianshu/new_module.py",
    )

    assert [finding.site for finding in findings] == [
        LaunchSite("src/tianshu/new_module.py", "outer", "subprocess.run")
    ]


@pytest.mark.parametrize(
    ("source", "resolved"),
    [
        (
            "from asyncio import create_subprocess_exec as spawn\nasync def f():\n await spawn('x')",
            "asyncio.create_subprocess_exec",
        ),
        (
            "from os import posix_spawnp as launch\ndef f():\n launch('x', [], {})",
            "os.posix_spawnp",
        ),
        ("import os\ndef f():\n os.forkpty()", "os.forkpty"),
        ("import subprocess\ndef f():\n subprocess.check_output(['x'])", "subprocess.check_output"),
    ],
)
def test_scanner_detects_supported_process_api_families(source: str, resolved: str) -> None:
    finding = _scan_source(source, path="src/tianshu/new_module.py")

    assert [item.site.resolved_call for item in finding] == [resolved]


@pytest.mark.parametrize(
    "source",
    [
        "from subprocess import *",
        "import importlib\nimportlib.import_module('subprocess')",
        "from importlib import import_module as load\nmodule_name = 'subprocess'\nload(module_name)",
        "loader = __import__\nloader('asyncio')",
    ],
)
def test_scanner_rejects_star_and_dynamic_import_bypasses(source: str) -> None:
    findings = _scan_source(source, path="src/tianshu/new_module.py")

    assert len(findings) == 1
    assert "can hide a process launch" in findings[0].reason


@pytest.mark.parametrize(
    "relative_path",
    ("src/tianshu/new_caller.py", "scripts/new_caller.py", "new_root_caller.py"),
)
def test_tree_scan_includes_new_python_files(tmp_path: Path, relative_path: str) -> None:
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import os\ndef launch():\n os.system('unsafe')\n",
        encoding="utf-8",
    )

    findings = _scan_tree(tmp_path)

    assert [finding.site.path for finding in findings] == [relative_path]


def test_allowlist_is_exact_and_stale_exemptions_fail() -> None:
    allowed = LaunchSite("src/tianshu/backend.py", "Backend.spawn", "subprocess.run")
    extra = LaunchSite("src/tianshu/backend.py", "Backend.other", "subprocess.run")
    findings = [
        Finding(allowed, line=10),
        Finding(allowed, line=11),
        Finding(extra, line=20),
    ]

    errors = _gate_errors(findings, (allowed, LaunchSite("gone.py", "gone", "os.execv")))

    assert len(errors) == 3
    assert any("backend.py:11" in error for error in errors)
    assert any("Backend.other" in error for error in errors)
    assert any("stale process exemption: gone.py" in error for error in errors)
