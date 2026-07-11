"""Keqing CLI execution must stream through the injected ExecutionGateway."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from tianshu.executor import execution_gateway as gateway
from tianshu.executor.capabilities import (
    claude_code_manifest,
    get_executor_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.keqing import KeqingExecutor
from tianshu.executor.keqing.adapter import ClaudeCodeAdapter
from tianshu.models import Edict, EdictRuntime, TaskStatus
from tianshu.models.governance_contract import (
    ExecutorSelectionV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind


class _StreamingHandle:
    def __init__(self, request) -> None:
        self.request = request
        assistant = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "bash"}]},
        }
        final = {
            "type": "result",
            "subtype": "success",
            "result": "completed through gateway",
            "usage": {"input_tokens": 5, "output_tokens": 3},
            "total_cost_usd": 0.01,
        }
        self.output = "\n".join((json.dumps(assistant), json.dumps(final))) + "\n"

    async def iter_stdout(self):
        midpoint = len(self.output) // 2
        yield self.output[:midpoint]
        yield self.output[midpoint:]

    async def wait(self):
        now = datetime.now(UTC)
        return gateway.ExecutionResult(
            stdout=self.output,
            stderr="",
            receipt=gateway.ExecutionReceipt(
                execution_id=self.request.execution_id,
                correlation_id=self.request.correlation_id,
                actor_id=self.request.actor.id,
                purpose=self.request.purpose,
                effective_contract_hash=self.request.effective_contract_hash,
                workspace_lease_id=self.request.workspace_lease_id,
                cwd=self.request.cwd,
                command_kind="argv",
                executable=self.request.command_argv[0],
                env_keys=(),
                secret_refs=(),
                network_mode=self.request.network.mode,
                sandbox_mode=self.request.sandbox.mode,
                sandbox_enforced=False,
                status="succeeded",
                started_at=now,
                finished_at=now,
                duration_ms=1,
                exit_code=0,
                terminating_signal=None,
                stdout_bytes=len(self.output.encode()),
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
            ),
        )


class _RecordingGateway:
    def __init__(self) -> None:
        self.requests = []

    async def start(self, request):
        self.requests.append(request)
        return _StreamingHandle(request)


@pytest.mark.asyncio
async def test_keqing_streams_through_gateway_and_emits_receipt(tmp_path, monkeypatch):
    argv = (
        "claude",
        "-p",
        "do the task",
        "--output-format",
        "stream-json",
        "--verbose",
    )
    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "build_argv",
        lambda self, _prompt, model=None: list(argv),
    )

    async def _direct_spawn_forbidden(*_args, **_kwargs):
        raise AssertionError("Keqing bypassed ExecutionGateway")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _direct_spawn_forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _direct_spawn_forbidden)

    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="do the task"),
            executor=ExecutorSelectionV1(adapter_id="keqing:claude-code"),
        ),
        claude_code_manifest(),
        probe_host_capabilities(),
    )
    context = gateway.ExecutionContext(
        correlation_id="memorial-keqing",
        actor=Principal(
            id="principal-keqing",
            kind=PrincipalKind.HUMAN,
            display_name="Keqing Principal",
        ),
        effective_contract=effective,
        workspace_lease_id="legacy-keqing",
    )
    recording_gateway = _RecordingGateway()
    executor = KeqingExecutor(
        root=tmp_path / "keqing",
        execution_gateway=recording_gateway,
    )
    edict = Edict(
        goal="do the task",
        submitter="principal-keqing",
        runtime=EdictRuntime(executor="keqing:claude-code"),
    )
    observed_events: list[dict] = []

    with gateway.bind_execution_context(context):
        result = await executor.execute(edict, on_event=observed_events.append)

    assert result.status == TaskStatus.COMPLETED
    assert result.result == "completed through gateway"
    assert observed_events == [{"type": "tool.called", "tool": "bash"}]
    assert result.events[-1]["type"] == "execution.receipt"
    assert result.events[-1]["receipt"]["effective_contract_hash"] == effective.content_hash
    assert len(recording_gateway.requests) == 1
    request = recording_gateway.requests[0]
    assert request.purpose == "keqing"
    assert request.argv_command.argv == argv
    assert request.command_grant.source == "system-adapter"
    assert request.command_grant.scope == "keqing"
    assert request.workspace_root == (tmp_path / "keqing" / edict.id).resolve()
    assert request.effective_contract == effective


def test_keqing_grant_requires_canonical_adapter_argv():
    actor = Principal(
        id="principal-keqing-authority",
        kind=PrincipalKind.SERVICE,
        display_name="Keqing Authority",
    )
    claude_effective = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="validate claude argv"),
            executor=ExecutorSelectionV1(adapter_id="keqing:claude-code"),
        ),
        get_executor_manifest("keqing:claude-code"),
        probe_host_capabilities(),
    )
    claude_context = gateway.ExecutionContext(
        correlation_id="memorial-keqing-authority",
        actor=actor,
        effective_contract=claude_effective,
        workspace_lease_id="legacy-keqing-authority",
    )
    with gateway.bind_execution_context(claude_context):
        with pytest.raises(gateway.ExecutionDenied, match="keqing_adapter_mismatch"):
            gateway.issue_keqing_command_grant(
                ("/bin/sh", "-c", "echo bypass"),
                backend="claude-code",
            )
        with pytest.raises(gateway.ExecutionDenied, match="keqing_adapter_mismatch"):
            gateway.issue_keqing_command_grant(
                ("claude", "--dangerously-skip-permissions", "do the task"),
                backend="claude-code",
            )
        claude_grant = gateway.issue_keqing_command_grant(
            ("claude", "-p", "do the task", "--output-format", "stream-json", "--verbose"),
            backend="claude-code",
        )

    codex_effective = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="validate codex argv"),
            executor=ExecutorSelectionV1(adapter_id="keqing:codex"),
        ),
        get_executor_manifest("keqing:codex"),
        probe_host_capabilities(),
    )
    codex_context = claude_context.model_copy(update={"effective_contract": codex_effective})
    with gateway.bind_execution_context(codex_context):
        codex_grant = gateway.issue_keqing_command_grant(
            ("codex", "exec", "--json", "do the task"),
            backend="codex",
        )

    assert claude_grant.scope == "keqing"
    assert codex_grant.scope == "keqing"
