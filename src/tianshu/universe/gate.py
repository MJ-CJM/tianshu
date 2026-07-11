"""Governed compile/import/test gate for Universe code variants."""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ulid import ULID

from tianshu.executor.execution_gateway import (
    ArgvCommand,
    EnvironmentPolicy,
    EnvironmentValue,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionReceipt,
    ExecutionRequest,
    ExecutionStartError,
    NetworkPolicy,
    SandboxRequirement,
    bind_execution_context,
    issue_universe_command_grant,
)
from tianshu.universe.execution import UniverseExecutionContextFactory


@dataclass(frozen=True)
class GateResult:
    passed: bool
    stage: str
    detail: str
    receipts: tuple[ExecutionReceipt, ...] = ()


class Gate:
    def __init__(
        self,
        execution_gateway: ExecutionGateway,
        *,
        context_factory: UniverseExecutionContextFactory,
        python_exe: str | None = None,
        timeout_s: int = 900,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("gate timeout must be positive")
        self.execution_gateway = execution_gateway
        self.context_factory = context_factory
        self._py = python_exe or sys.executable
        self._timeout = timeout_s
        self.last_receipts: tuple[ExecutionReceipt, ...] = ()

    async def run_async(self, worktree: Path, *, run_tests: bool = True) -> GateResult:
        wt = Path(worktree).resolve()
        environment = EnvironmentPolicy(
            allow_names=(),
            values=(EnvironmentValue(name="PYTHONPATH", value=str(wt / "src")),),
        )
        context = self.context_factory.create(
            operation="gate",
            timeout_seconds=self._timeout,
        )
        stages: list[tuple[str, tuple[str, ...]]] = [
            ("static", (self._py, "-m", "compileall", "-q", str(wt / "src"))),
            ("import", (self._py, "-c", "import tianshu")),
        ]
        if run_tests:
            stages.append(("test", (self._py, "-m", "pytest", "-q")))

        receipts: list[ExecutionReceipt] = []
        deadline = time.monotonic() + self._timeout
        for stage, argv in stages:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result = GateResult(
                    False,
                    stage,
                    f"timeout after {self._timeout}s",
                    tuple(receipts),
                )
                self.last_receipts = result.receipts
                return result
            stage_id = f"gate:{stage}"
            with bind_execution_context(context):
                grant = issue_universe_command_grant(
                    stage=stage_id,
                    argv=argv,
                    workspace_root=wt,
                    cwd=".",
                    environment=environment,
                )
            request = ExecutionRequest(
                execution_id=str(ULID()),
                correlation_id=context.correlation_id,
                actor=context.actor,
                purpose="universe_gate",
                universe_stage=stage_id,
                effective_contract=context.effective_contract,
                argv_command=ArgvCommand(argv=argv),
                workspace_lease_id=context.workspace_lease_id,
                workspace_root=wt,
                cwd=".",
                environment=environment,
                network=NetworkPolicy(
                    mode=(
                        "unrestricted"
                        if self.context_factory.security_mode == "trusted-local"
                        else "deny"
                    )
                ),
                timeout_seconds=remaining,
                stdout_limit_bytes=4096,
                stderr_limit_bytes=4096,
                sandbox=SandboxRequirement(
                    trust_level=self.context_factory.security_mode,
                    mode=(
                        "host"
                        if self.context_factory.security_mode == "trusted-local"
                        else "required"
                    ),
                    allow_host=self.context_factory.security_mode == "trusted-local",
                ),
                command_grant=grant,
            )
            try:
                execution = await self.execution_gateway.run(request)
            except ExecutionStartError as exc:
                receipts.append(exc.receipt)
                result = GateResult(False, stage, _tail(str(exc)), tuple(receipts))
                self.last_receipts = result.receipts
                return result
            except ExecutionDenied as exc:
                if exc.receipt is None:
                    raise
                receipts.append(exc.receipt)
                result = GateResult(False, stage, _tail(str(exc)), tuple(receipts))
                self.last_receipts = result.receipts
                return result
            receipts.append(execution.receipt)
            if execution.receipt.status != "succeeded":
                output = execution.stdout + execution.stderr
                detail = output or execution.error or "gate stage failed"
                result = GateResult(False, stage, _tail(detail), tuple(receipts))
                self.last_receipts = result.receipts
                return result

        result = GateResult(True, "ok", "", tuple(receipts))
        self.last_receipts = result.receipts
        return result

    def run(self, worktree: Path, *, run_tests: bool = True) -> GateResult:
        """Compatibility wrapper for CLI/tests outside an active event loop."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async(worktree, run_tests=run_tests))
        raise RuntimeError("Gate.run() cannot run inside an event loop; await run_async()")


def _tail(text: str, n: int = 2000) -> str:
    return text[-n:] if len(text) > n else text
