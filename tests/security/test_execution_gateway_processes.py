"""Real-process lifecycle coverage for the governed execution boundary."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import (
    ArgvCommand,
    EnvironmentPolicy,
    ExecutionContext,
    ExecutionGateway,
    ExecutionRequest,
    NetworkPolicy,
    SandboxRequirement,
    _issue_tool_argv_grant,
    _issue_tool_policy_decision,
    bind_execution_context,
    bind_tool_policy_decision,
)
from tianshu.models.governance_contract import (
    NetworkPolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind


@pytest.fixture(scope="module")
def effective_contract():
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="exercise process lifecycle"),
        network=NetworkPolicyV1(mode="unrestricted_requested"),
    )
    return resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )


def _request(
    tmp_path: Path,
    effective_contract,
    argv: tuple[str, ...],
    *,
    timeout: float = 3,
    output_limit: int = 4096,
) -> ExecutionRequest:
    actor = Principal(
        id="process-principal",
        kind=PrincipalKind.SERVICE,
        display_name="Process Test",
        scopes=frozenset({"api"}),
    )
    context = ExecutionContext(
        correlation_id="process-correlation",
        actor=actor,
        effective_contract=effective_contract,
        workspace_lease_id="process-workspace",
    )
    arguments = {"argv": list(argv)}
    with bind_execution_context(context):
        decision = _issue_tool_policy_decision("gateway-process-test", arguments)
        with bind_tool_policy_decision(decision):
            grant = _issue_tool_argv_grant("gateway-process-test", arguments, argv)
    return ExecutionRequest(
        execution_id="process-test",
        correlation_id="process-correlation",
        actor=actor,
        purpose="tool",
        effective_contract=effective_contract,
        argv_command=ArgvCommand(argv=argv),
        workspace_lease_id="process-workspace",
        workspace_root=tmp_path,
        cwd=".",
        environment=EnvironmentPolicy(),
        network=NetworkPolicy(mode="unrestricted"),
        timeout_seconds=timeout,
        stdout_limit_bytes=output_limit,
        stderr_limit_bytes=output_limit,
        sandbox=SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        command_grant=grant,
    )


@pytest.mark.asyncio
async def test_large_stdout_and_stderr_are_drained_concurrently_and_bounded(
    tmp_path,
    effective_contract,
):
    size = 512 * 1024
    limit = 16 * 1024
    script = f"import os;data=b'x'*{size};os.write(1,data);os.write(2,data)"
    argv = (sys.executable, "-c", script)

    result = await ExecutionGateway().run(
        _request(tmp_path, effective_contract, argv, output_limit=limit)
    )

    assert result.receipt.status == "succeeded"
    assert result.receipt.stdout_bytes == size
    assert result.receipt.stderr_bytes == size
    assert result.receipt.stdout_truncated is True
    assert result.receipt.stderr_truncated is True
    assert len(result.stdout.encode()) == limit
    assert len(result.stderr.encode()) == limit


@pytest.mark.asyncio
async def test_timeout_reports_receipt_and_kills_the_whole_process_group(
    tmp_path,
    effective_contract,
):
    child_pid_file = tmp_path / "timeout-child.pid"
    script = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    argv = (sys.executable, "-c", script)

    result = await ExecutionGateway(termination_grace_seconds=0.05).run(
        _request(tmp_path, effective_contract, argv, timeout=0.3)
    )

    assert result.receipt.status == "timed_out"
    assert result.receipt.exit_code is None
    assert result.receipt.terminating_signal in {signal.SIGTERM, signal.SIGKILL}
    assert result.error == "execution timed out after 0.3s"
    child_pid = int(child_pid_file.read_text())
    await _assert_pid_gone(child_pid)


@pytest.mark.asyncio
async def test_cancelling_wait_kills_the_whole_process_group(
    tmp_path,
    effective_contract,
):
    child_pid_file = tmp_path / "cancel-child.pid"
    script = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    argv = (sys.executable, "-c", script)
    handle = await ExecutionGateway(termination_grace_seconds=0.05).start(
        _request(tmp_path, effective_contract, argv, timeout=10)
    )
    await _wait_for_file(child_pid_file)
    child_pid = int(child_pid_file.read_text())

    waiter = asyncio.create_task(handle.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await _assert_pid_gone(child_pid)


@pytest.mark.asyncio
async def test_exit_code_and_signal_are_distinct_in_receipt(tmp_path, effective_contract):
    failed_argv = (sys.executable, "-c", "raise SystemExit(7)")
    failed = await ExecutionGateway().run(_request(tmp_path, effective_contract, failed_argv))
    assert failed.receipt.status == "failed"
    assert failed.receipt.exit_code == 7
    assert failed.receipt.terminating_signal is None
    assert failed.returncode == 7

    signal_argv = (
        sys.executable,
        "-c",
        "import os,signal;os.kill(os.getpid(),signal.SIGTERM)",
    )
    signalled = await ExecutionGateway().run(_request(tmp_path, effective_contract, signal_argv))
    assert signalled.receipt.status == "failed"
    assert signalled.receipt.exit_code is None
    assert signalled.receipt.terminating_signal == signal.SIGTERM
    assert signalled.returncode == -signal.SIGTERM


@pytest.mark.asyncio
async def test_start_streams_stdout_before_process_exit(tmp_path, effective_contract):
    script = "import time;print('first',flush=True);time.sleep(1);print('second',flush=True)"
    argv = (sys.executable, "-c", script)
    handle = await ExecutionGateway().start(_request(tmp_path, effective_contract, argv, timeout=3))
    stream = handle.iter_stdout()

    first = await asyncio.wait_for(anext(stream), timeout=0.5)

    assert first == "first\n"
    result = await handle.wait()
    assert result.receipt.status == "succeeded"
    assert result.stdout == "first\nsecond\n"


@pytest.mark.asyncio
async def test_deadline_kills_descendant_after_process_group_leader_exits(
    tmp_path,
    effective_contract,
):
    child_pid_file = tmp_path / "leader-exit-child.pid"
    script = (
        "import pathlib,subprocess,sys;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(1)']);"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))"
    )
    argv = (sys.executable, "-c", script)
    started = time.monotonic()

    result = await ExecutionGateway(termination_grace_seconds=0.05).run(
        _request(tmp_path, effective_contract, argv, timeout=0.2)
    )

    assert time.monotonic() - started < 0.8
    assert result.receipt.status == "timed_out"
    child_pid = int(child_pid_file.read_text())
    await _assert_pid_gone(child_pid)


async def _wait_for_file(path: Path) -> None:
    for _ in range(100):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"child pid file was not created: {path}")


async def _assert_pid_gone(pid: int) -> None:
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"orphan child process remains alive: {pid}")
