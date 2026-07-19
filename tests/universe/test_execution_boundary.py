"""Gate and SandboxRunner use the injected Universe execution boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.universe.execution import UniverseExecutionContextFactory
from tianshu.universe.gate import Gate
from tianshu.universe.sandbox import SandboxRunner


def test_gate_and_sandbox_require_the_shared_gateway() -> None:
    gateway = ExecutionGateway()
    factory = UniverseExecutionContextFactory(security_mode="trusted-local")

    gate = Gate(gateway, context_factory=factory)
    sandbox = SandboxRunner(gateway, context_factory=factory)

    assert gate.execution_gateway is gateway
    assert sandbox.execution_gateway is gateway
    assert gate.context_factory is sandbox.context_factory is factory


@pytest.mark.parametrize(
    "module_name",
    ("gate.py", "sandbox.py", "sandbox_container.py"),
)
def test_migrated_universe_modules_have_no_direct_process_launch(module_name: str) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "tianshu" / "universe" / module_name).read_text()
    tree = ast.parse(source)

    forbidden_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.name == "subprocess"
    }
    forbidden_calls = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func)
        in {
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "os.system",
            "os.popen",
            "subprocess.run",
            "subprocess.Popen",
        }
    }

    assert forbidden_imports == set()
    assert forbidden_calls == set()
