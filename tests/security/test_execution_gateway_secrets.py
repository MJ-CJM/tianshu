"""Environment allowlisting and end-to-end secret redaction."""

from __future__ import annotations

import asyncio
import json
import sys
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
    EnvironmentSecretRef,
    EnvironmentValue,
    ExecutionContext,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionRequest,
    ExecutionStartError,
    NetworkPolicy,
    SandboxRequirement,
    _issue_tool_argv_grant,
    _issue_tool_policy_decision,
    _SecretStreamRedactor,
    bind_execution_context,
    bind_tool_policy_decision,
)
from tianshu.models.governance_contract import (
    NetworkPolicyV1,
    ObjectiveV1,
    PermissionPolicyV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind

_SECRET_REF = "GATEWAY_TEST_SECRET_REF"
_SECRET_ENV_NAME = "APP_BOUND_SECRET"
_SENTINEL = "violet-otter-7319-boundary-sentinel"


@pytest.fixture(scope="module")
def effective_contract():
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="exercise secret boundary"),
        permissions=PermissionPolicyV1(secret_refs=(_SECRET_REF,)),
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
    environment: EnvironmentPolicy,
) -> ExecutionRequest:
    actor = Principal(
        id="secret-principal",
        kind=PrincipalKind.SERVICE,
        display_name="Secret Test",
    )
    context = ExecutionContext(
        correlation_id="secret-correlation",
        actor=actor,
        effective_contract=effective_contract,
        workspace_lease_id="secret-workspace",
    )
    arguments = {"argv": list(argv)}
    with bind_execution_context(context):
        decision = _issue_tool_policy_decision("gateway-secret-test", arguments)
        with bind_tool_policy_decision(decision):
            grant = _issue_tool_argv_grant("gateway-secret-test", arguments, argv)
    return ExecutionRequest(
        execution_id="secret-test",
        correlation_id="secret-correlation",
        actor=actor,
        purpose="tool",
        effective_contract=effective_contract,
        argv_command=ArgvCommand(argv=argv),
        workspace_lease_id="secret-workspace",
        workspace_root=tmp_path,
        cwd=".",
        environment=environment,
        network=NetworkPolicy(mode="unrestricted"),
        timeout_seconds=2,
        stdout_limit_bytes=8192,
        stderr_limit_bytes=8192,
        sandbox=SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        command_grant=grant,
    )


def _secret_policy() -> EnvironmentPolicy:
    return EnvironmentPolicy(
        allow_names=("VISIBLE_VALUE",),
        secret_refs=(EnvironmentSecretRef(env_name=_SECRET_ENV_NAME, ref=_SECRET_REF),),
    )


def test_stream_redactor_does_not_hold_complete_non_secret_protocol_frames() -> None:
    frame = b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
    redactor = _SecretStreamRedactor((_SENTINEL,))

    assert redactor.feed(frame) == frame


def test_exact_secret_value_cannot_reappear_inside_redaction_marker() -> None:
    secret = b"SECRET"
    redactor = _SecretStreamRedactor((secret.decode(),))

    redacted = redactor.feed(b"value=" + secret)

    assert secret not in redacted


@pytest.mark.asyncio
async def test_allowlisted_env_only_and_secret_absent_from_result_receipt_and_logs(
    tmp_path,
    effective_contract,
    monkeypatch,
    caplog,
):
    monkeypatch.setenv("VISIBLE_VALUE", "visible")
    monkeypatch.setenv("UNLISTED_VALUE", "must-not-reach-child")
    monkeypatch.setenv(_SECRET_REF, _SENTINEL)
    script = (
        "import json,os,sys;"
        "assert os.environ.get('VISIBLE_VALUE')=='visible';"
        "assert 'UNLISTED_VALUE' not in os.environ;"
        f"assert os.environ.get({_SECRET_ENV_NAME!r})=={_SENTINEL!r};"
        f"print({_SENTINEL!r});"
        f"print({_SENTINEL!r},file=sys.stderr);"
        "print(json.dumps(sorted(k for k in os.environ if k.startswith('TIANSHU_'))))"
    )
    argv = (sys.executable, "-c", script)

    result = await ExecutionGateway().run(
        _request(tmp_path, effective_contract, argv, _secret_policy())
    )

    serialized = result.model_dump_json()
    assert result.receipt.status == "succeeded"
    assert _SENTINEL not in result.stdout
    assert _SENTINEL not in result.stderr
    assert _SENTINEL not in serialized
    assert _SENTINEL not in repr(result)
    assert _SECRET_REF in result.receipt.secret_refs
    assert _SECRET_ENV_NAME in result.receipt.env_keys
    assert all(_SENTINEL not in record.getMessage() for record in caplog.records)
    assert "must-not-reach-child" not in serialized


class _NoSpawnBackend:
    supports_sandbox = False
    supports_network_enforcement = False

    def __init__(self) -> None:
        self.spawned = False

    async def spawn(self, **_kwargs):
        self.spawned = True
        raise AssertionError("spawn must not be reached")


@pytest.mark.asyncio
async def test_secret_like_env_name_cannot_bypass_secret_refs(
    tmp_path,
    effective_contract,
    monkeypatch,
):
    monkeypatch.setenv("TIANSHU_GATEWAY_SECRET", _SENTINEL)
    argv = (sys.executable, "-c", "print('unreachable')")
    request = _request(
        tmp_path,
        effective_contract,
        argv,
        EnvironmentPolicy(allow_names=("TIANSHU_GATEWAY_SECRET",)),
    )
    backend = _NoSpawnBackend()

    with pytest.raises(ExecutionDenied, match="environment"):
        await ExecutionGateway(backend=backend).run(request)
    assert backend.spawned is False


@pytest.mark.asyncio
async def test_non_universe_purpose_cannot_use_literal_environment_values(
    tmp_path,
    effective_contract,
):
    argv = (sys.executable, "-c", "print('unreachable')")
    request = _request(
        tmp_path,
        effective_contract,
        argv,
        EnvironmentPolicy(
            allow_names=(),
            values=(EnvironmentValue(name="PYTHONPATH", value="/tmp/literal"),),
        ),
    )
    backend = _NoSpawnBackend()

    with pytest.raises(ExecutionDenied, match="literal_values_not_allowed"):
        await ExecutionGateway(backend=backend).run(request)
    assert backend.spawned is False


@pytest.mark.asyncio
async def test_streaming_redaction_handles_secret_split_across_pipe_reads(
    tmp_path,
    effective_contract,
    monkeypatch,
):
    monkeypatch.setenv(_SECRET_REF, _SENTINEL)
    midpoint = len(_SENTINEL) // 2
    script = (
        "import os,time;"
        f"value=os.environ[{_SECRET_ENV_NAME!r}];"
        f"os.write(1,value[:{midpoint}].encode());"
        "time.sleep(0.2);"
        f"os.write(1,value[{midpoint}:].encode())"
    )
    argv = (sys.executable, "-c", script)
    handle = await ExecutionGateway().start(
        _request(tmp_path, effective_contract, argv, _secret_policy())
    )

    streamed = "".join([chunk async for chunk in handle.iter_stdout()])
    result = await handle.wait()

    assert _SENTINEL not in streamed
    assert _SENTINEL not in result.stdout
    assert _SENTINEL not in result.model_dump_json()


class _SecretLeakingGuard:
    name = "secret_leaking_guard"

    async def evaluate(self, _request):
        raise RuntimeError(f"guard accidentally included {_SENTINEL}")


@pytest.mark.asyncio
async def test_secret_is_redacted_from_guard_and_spawn_exceptions(
    tmp_path,
    effective_contract,
    monkeypatch,
):
    monkeypatch.setenv(_SECRET_REF, _SENTINEL)
    argv = (sys.executable, "-c", "print('unreachable')")
    request = _request(tmp_path, effective_contract, argv, _secret_policy())

    with pytest.raises(ExecutionDenied) as guard_error:
        await ExecutionGateway(mandatory_guards=(_SecretLeakingGuard(),)).run(request)
    assert _SENTINEL not in str(guard_error.value)

    class _FailingBackend:
        supports_sandbox = False
        supports_network_enforcement = False

        async def spawn(self, **kwargs):
            assert kwargs["env"][_SECRET_ENV_NAME] == _SENTINEL
            raise RuntimeError(f"spawn failed while handling {_SENTINEL}")

    with pytest.raises(ExecutionStartError) as spawn_error:
        await ExecutionGateway(backend=_FailingBackend()).run(request)
    assert _SENTINEL not in str(spawn_error.value)
    assert _SENTINEL not in json.dumps(spawn_error.value.args)
    assert spawn_error.value.receipt.status == "failed"
    assert spawn_error.value.receipt.exit_code is None
    assert spawn_error.value.receipt.stdout_bytes == 0
    assert spawn_error.value.receipt.stderr_bytes == 0
    assert _SENTINEL not in spawn_error.value.receipt.model_dump_json()


@pytest.mark.asyncio
async def test_backend_spawn_cancellation_is_not_converted_to_start_failure(
    tmp_path,
    effective_contract,
):
    argv = (sys.executable, "-c", "print('unreachable')")
    request = _request(tmp_path, effective_contract, argv, EnvironmentPolicy())
    entered = asyncio.Event()

    class _CancelledBackend:
        backend_id = "cancelled-backend"
        supports_sandbox = False
        supports_network_enforcement = False

        async def spawn(self, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(ExecutionGateway(backend=_CancelledBackend()).start(request))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_settings_secret_namespace_is_reserved_for_universe_execution(
    tmp_path,
):
    secret_ref = "settings:eval_llm_api_key"
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="reject settings namespace from tools"),
        permissions=PermissionPolicyV1(secret_refs=(secret_ref,)),
        network=NetworkPolicyV1(mode="unrestricted_requested"),
    )
    effective = resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )
    argv = (sys.executable, "-c", "print('unreachable')")
    environment = EnvironmentPolicy(
        allow_names=(),
        secret_refs=(EnvironmentSecretRef(env_name="APP_BOUND_SECRET", ref=secret_ref),),
    )
    resolved: list[str] = []

    def resolver(ref: str) -> str | None:
        resolved.append(ref)
        return _SENTINEL

    backend = _NoSpawnBackend()
    with pytest.raises(ExecutionDenied, match="reserved_secret_namespace"):
        await ExecutionGateway(backend=backend, secret_resolver=resolver).start(
            _request(tmp_path, effective, argv, environment)
        )

    assert resolved == []
    assert backend.spawned is False
