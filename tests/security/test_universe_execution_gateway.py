"""Universe-specific command admission and sandbox truthfulness."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from tianshu.executor.execution_gateway import (
    ArgvCommand,
    EnvironmentPolicy,
    EnvironmentSecretRef,
    EnvironmentValue,
    ExecutionContext,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionRequest,
    NetworkPolicy,
    SandboxRequirement,
    SpawnedProcess,
    bind_execution_context,
    issue_universe_command_grant,
)
from tianshu.models.governance_contract import ObjectiveV1
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.universe.execution import UniverseExecutionContextFactory


class _NoSpawnBackend:
    backend_id = "no-spawn"
    supports_sandbox = False
    supports_network_enforcement = False

    def __init__(self) -> None:
        self.spawned = False

    async def spawn(self, **_kwargs):
        self.spawned = True
        raise AssertionError("invalid Universe request reached process spawn")


def _admitted_request(
    tmp_path: Path,
    *,
    stage: str = "gate:import",
    cwd: str = ".",
    environment: EnvironmentPolicy | None = None,
) -> tuple[ExecutionRequest, UniverseExecutionContextFactory]:
    environment = environment or EnvironmentPolicy(
        allow_names=(),
        values=(EnvironmentValue(name="PYTHONPATH", value=str(tmp_path / "src")),),
    )
    factory = UniverseExecutionContextFactory(security_mode="trusted-local")
    context = factory.create(
        operation="gate",
        timeout_seconds=10,
        secret_refs=tuple(ref.ref for ref in environment.secret_refs),
    )
    argv = (sys.executable, "-c", "import tianshu")
    with bind_execution_context(context):
        grant = issue_universe_command_grant(
            stage=stage,
            argv=argv,
            cwd=cwd,
            environment=environment,
        )
    request = ExecutionRequest(
        execution_id="universe-test",
        correlation_id=context.correlation_id,
        actor=context.actor,
        purpose="universe_gate",
        universe_stage=stage,
        effective_contract=context.effective_contract,
        argv_command=ArgvCommand(argv=argv),
        workspace_lease_id=context.workspace_lease_id,
        workspace_root=tmp_path,
        cwd=cwd,
        environment=environment,
        network=NetworkPolicy(mode="unrestricted"),
        timeout_seconds=10,
        stdout_limit_bytes=2048,
        stderr_limit_bytes=2048,
        sandbox=SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        command_grant=grant,
    )
    return request, factory


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("universe_stage", "gate:test"),
        ("cwd", "other"),
        (
            "environment",
            EnvironmentPolicy(
                allow_names=(),
                values=(EnvironmentValue(name="PYTHONPATH", value="changed"),),
            ),
        ),
    ],
)
async def test_universe_grant_rejects_stage_cwd_and_environment_mismatch_before_spawn(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    (tmp_path / "other").mkdir()
    request, _ = _admitted_request(tmp_path)
    backend = _NoSpawnBackend()

    with pytest.raises(ExecutionDenied, match="command_grant"):
        await ExecutionGateway(backend=backend).run(request.model_copy(update={field: replacement}))

    assert backend.spawned is False


@pytest.mark.asyncio
async def test_trusted_local_host_fallback_is_explicit_and_visible_in_receipt(
    tmp_path: Path,
) -> None:
    request, _ = _admitted_request(tmp_path)

    result = await ExecutionGateway().run(request)

    assert result.receipt.status == "succeeded"
    assert result.receipt.sandbox_enforced is False
    assert any(
        gap.code == "sandbox_unavailable_host_fallback" for gap in result.receipt.advisory_gaps
    )


@pytest.mark.asyncio
async def test_secure_remote_requires_required_sandbox_without_host_fallback(
    tmp_path: Path,
) -> None:
    request, _ = _admitted_request(tmp_path)
    backend = _NoSpawnBackend()
    secure = request.model_copy(
        update={
            "sandbox": SandboxRequirement(
                trust_level="secure-remote",
                mode="host",
                allow_host=True,
            )
        }
    )

    with pytest.raises(ExecutionDenied, match="secure_remote"):
        await ExecutionGateway(backend=backend).run(secure)

    assert backend.spawned is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ("host", "preferred"))
async def test_secure_remote_rejects_host_and_preferred_modes_before_spawn(
    tmp_path: Path,
    mode: str,
) -> None:
    request, _ = _admitted_request(tmp_path)
    backend = _NoSpawnBackend()

    with pytest.raises(ExecutionDenied, match="secure_remote_policy_invalid"):
        await ExecutionGateway(backend=backend).run(
            request.model_copy(
                update={
                    "sandbox": SandboxRequirement(
                        trust_level="secure-remote",
                        mode=mode,
                        allow_host=mode == "host",
                    )
                }
            )
        )

    assert backend.spawned is False


@pytest.mark.asyncio
async def test_secure_remote_required_mode_rejects_missing_backend_before_spawn(
    tmp_path: Path,
) -> None:
    request, _ = _admitted_request(tmp_path)
    backend = _NoSpawnBackend()

    with pytest.raises(ExecutionDenied, match="secure_remote_unavailable"):
        await ExecutionGateway(backend=backend).run(
            request.model_copy(
                update={
                    "sandbox": SandboxRequirement(
                        trust_level="secure-remote",
                        mode="required",
                        allow_host=False,
                    )
                }
            )
        )

    assert backend.spawned is False


class _DishonestSandboxBackend:
    backend_id = "dishonest"
    supports_sandbox = True
    supports_network_enforcement = False

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None

    async def spawn(self, **kwargs):
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time;time.sleep(60)",
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        return SpawnedProcess(
            process=self.process,
            backend_id=self.backend_id,
            network_enforced=False,
            sandbox_enforced=False,
        )


@pytest.mark.asyncio
async def test_dishonest_secure_remote_backend_is_reaped(
    tmp_path: Path,
) -> None:
    request, _ = _admitted_request(tmp_path)
    backend = _DishonestSandboxBackend()

    with pytest.raises(ExecutionDenied, match="backend_enforcement_unproven") as error:
        await ExecutionGateway(backend=backend).run(
            request.model_copy(
                update={
                    "sandbox": SandboxRequirement(
                        trust_level="secure-remote",
                        mode="required",
                        allow_host=False,
                    )
                }
            )
        )

    assert backend.process is not None
    assert backend.process.returncode is not None
    assert error.value.receipt is not None
    assert error.value.receipt.status == "failed"
    assert error.value.receipt.backend_id == "dishonest"


@pytest.mark.asyncio
async def test_universe_grant_rejects_argv_contract_correlation_and_actor_mismatch(
    tmp_path: Path,
) -> None:
    request, _ = _admitted_request(tmp_path)
    variants = (
        request.model_copy(
            update={"argv_command": ArgvCommand(argv=(sys.executable, "-c", "pass"))}
        ),
        request.model_copy(
            update={
                "effective_contract": request.effective_contract.model_copy(
                    update={"objective": ObjectiveV1(goal="different contract")}
                )
            }
        ),
        request.model_copy(update={"correlation_id": "different-correlation"}),
        request.model_copy(
            update={
                "actor": Principal(
                    id="different-actor",
                    kind=PrincipalKind.SERVICE,
                    display_name="Different Actor",
                )
            }
        ),
    )

    for variant in variants:
        backend = _NoSpawnBackend()
        with pytest.raises(ExecutionDenied, match="command_grant"):
            await ExecutionGateway(backend=backend).run(variant)
        assert backend.spawned is False


def test_literal_secret_cannot_enter_environment_model_or_request_dump() -> None:
    sentinel = "violet-literal-secret"

    with pytest.raises(ValidationError, match="secret reference") as error:
        EnvironmentValue(name="TIANSHU_LLM_API_KEY", value=sentinel)

    assert sentinel not in str(error.value)


def test_universe_stage_grant_rejects_noncanonical_command(tmp_path: Path) -> None:
    environment = EnvironmentPolicy(
        allow_names=(),
        values=(EnvironmentValue(name="PYTHONPATH", value=str(tmp_path / "src")),),
    )
    context = UniverseExecutionContextFactory(security_mode="trusted-local").create(
        operation="gate",
        timeout_seconds=10,
    )

    with (
        bind_execution_context(context),
        pytest.raises(ExecutionDenied, match="universe_command_not_canonical"),
    ):
        issue_universe_command_grant(
            stage="gate:import",
            argv=(sys.executable, "-c", "print('not the import gate')"),
            cwd=".",
            environment=environment,
        )


@pytest.mark.asyncio
async def test_universe_rejects_arbitrary_literal_environment_name_before_spawn(
    tmp_path: Path,
) -> None:
    request, _ = _admitted_request(tmp_path)
    environment = EnvironmentPolicy(
        allow_names=(),
        values=(EnvironmentValue(name="ARBITRARY_VALUE", value="not-admitted"),),
    )
    context = ExecutionContext(
        correlation_id=request.correlation_id,
        actor=request.actor,
        effective_contract=request.effective_contract,
        workspace_lease_id=request.workspace_lease_id,
    )
    with bind_execution_context(context):
        grant = issue_universe_command_grant(
            stage="gate:import",
            argv=request.command_argv,
            cwd=".",
            environment=environment,
        )
    backend = _NoSpawnBackend()

    with pytest.raises(ExecutionDenied, match="literal_name_not_allowed"):
        await ExecutionGateway(backend=backend).run(
            request.model_copy(update={"environment": environment, "command_grant": grant})
        )

    assert backend.spawned is False


@pytest.mark.asyncio
async def test_universe_clean_environment_and_secret_ref_are_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "violet-universe-secret"
    secret_ref = "settings:eval_llm_api_key"
    monkeypatch.setenv("HOST_UNRELATED_SECRET", "must-not-reach-child")
    environment = EnvironmentPolicy(
        allow_names=(),
        values=(EnvironmentValue(name="PYTHONPATH", value=str(tmp_path / "src")),),
        secret_refs=(EnvironmentSecretRef(env_name="TIANSHU_LLM_API_KEY", ref=secret_ref),),
    )
    factory = UniverseExecutionContextFactory(security_mode="trusted-local")
    context = factory.create(
        operation="gate",
        timeout_seconds=10,
        secret_refs=(secret_ref,),
    )
    package = tmp_path / "src" / "tianshu"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "import os\n"
        "assert 'HOST_UNRELATED_SECRET' not in os.environ\n"
        "print(os.environ['TIANSHU_LLM_API_KEY'])\n"
    )
    argv = (sys.executable, "-c", "import tianshu")
    with bind_execution_context(context):
        grant = issue_universe_command_grant(
            stage="gate:import",
            argv=argv,
            cwd=".",
            environment=environment,
        )
    request = ExecutionRequest(
        execution_id="universe-secret-test",
        correlation_id=context.correlation_id,
        actor=context.actor,
        purpose="universe_gate",
        universe_stage="gate:import",
        effective_contract=context.effective_contract,
        argv_command=ArgvCommand(argv=argv),
        workspace_lease_id=context.workspace_lease_id,
        workspace_root=tmp_path,
        cwd=".",
        environment=environment,
        network=NetworkPolicy(mode="unrestricted"),
        timeout_seconds=10,
        stdout_limit_bytes=2048,
        stderr_limit_bytes=2048,
        sandbox=SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        command_grant=grant,
    )

    assert sentinel not in request.model_dump_json()
    result = await ExecutionGateway(
        secret_resolver=lambda ref: sentinel if ref == secret_ref else None
    ).run(request)

    assert result.receipt.status == "succeeded"
    assert sentinel not in result.stdout
    assert sentinel not in result.model_dump_json()
    assert result.receipt.secret_refs == (secret_ref,)
