"""Regression guard for the complete architectural layer contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_LAYERS = [
    "tianshu.gateway : tianshu.executor : tianshu.scheduler : tianshu.bootstrap : tianshu.universe",
    "tianshu.application : tianshu.evolution : tianshu.evidence : tianshu.plugins",
    "tianshu.storage : tianshu.secrets : tianshu.memory : tianshu.persona : tianshu.skills",
    "tianshu.kernel : tianshu.models : tianshu.config : tianshu.bus",
]
_EXPECTED_IGNORES = ["tianshu.kernel.ambient -> tianshu.persona.model"]


def test_complete_layer_contract_cannot_be_silently_weakened() -> None:
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = config["tool"]["importlinter"]["contracts"]
    layer_contract = next(
        contract for contract in contracts if contract["name"] == "分层:底层契约不得向上依赖"
    )

    assert layer_contract["type"] == "layers"
    assert layer_contract["layers"] == _EXPECTED_LAYERS
    assert layer_contract["ignore_imports"] == _EXPECTED_IGNORES


def test_compatibility_exports_share_canonical_objects() -> None:
    from tianshu.application.managed_attempt import (
        AttemptAuthority as CanonicalAttemptAuthority,
    )
    from tianshu.application.managed_attempt import (
        ManagedRunSuspended as CanonicalManagedRunSuspended,
    )
    from tianshu.application.run_dispatcher import (
        AttemptAuthority as LegacyAttemptAuthority,
    )
    from tianshu.application.runtime_router import ChallengerRouter as CanonicalRouter
    from tianshu.evidence.models import ArtifactRefV1 as LegacyArtifactRef
    from tianshu.evolution.gates import EvolutionGateReportV1 as LegacyGateReport
    from tianshu.evolution.runtime_context import (
        EvolutionRuntimeContext as LegacyRuntimeContext,
    )
    from tianshu.executor.adapters import ExecutorGenerationUnavailable as LegacyError
    from tianshu.executor.generation_controller import (
        GenerationRecoveryReport as LegacyRecoveryReport,
    )
    from tianshu.executor.keqing.generation import PI_GENERATION_SCOPE as LegacyPiScope
    from tianshu.executor.managed_tools import (
        ManagedRunSuspended as LegacyManagedRunSuspended,
    )
    from tianshu.executor.managed_tools import (
        get_managed_attempt_authority as legacy_authority_context,
    )
    from tianshu.executor.workspace_policy import (
        validate_workspace_policy as legacy_workspace_policy,
    )
    from tianshu.models.evidence import ArtifactRefV1 as CanonicalArtifactRef
    from tianshu.models.evolution_gate import EvolutionGateReportV1 as CanonicalGateReport
    from tianshu.models.executor_generation import PI_GENERATION_SCOPE as CanonicalPiScope
    from tianshu.models.executor_generation import (
        ExecutorGenerationUnavailable as CanonicalError,
    )
    from tianshu.models.executor_generation import (
        GenerationRecoveryReport as CanonicalRecoveryReport,
    )
    from tianshu.models.runtime_context import (
        EvolutionRuntimeContext as CanonicalRuntimeContext,
    )
    from tianshu.models.tool_definition import ToolDefinition as CanonicalToolDefinition
    from tianshu.models.workspace_policy import (
        validate_workspace_policy as canonical_workspace_policy,
    )
    from tianshu.tools.registry import ToolDefinition as LegacyToolDefinition
    from tianshu.universe.router import ChallengerRouter as LegacyRouter

    assert LegacyAttemptAuthority is CanonicalAttemptAuthority
    assert LegacyManagedRunSuspended is CanonicalManagedRunSuspended
    assert LegacyArtifactRef is CanonicalArtifactRef
    assert LegacyGateReport is CanonicalGateReport
    assert LegacyRuntimeContext is CanonicalRuntimeContext
    assert LegacyError is CanonicalError
    assert LegacyRecoveryReport is CanonicalRecoveryReport
    assert LegacyPiScope is CanonicalPiScope
    assert LegacyRouter is CanonicalRouter
    assert LegacyToolDefinition is CanonicalToolDefinition
    assert legacy_workspace_policy is canonical_workspace_policy

    authority = CanonicalAttemptAuthority("attempt", "memorial", "owner", 1)
    from tianshu.application.managed_attempt import bind_managed_attempt_authority

    with bind_managed_attempt_authority(authority):
        assert legacy_authority_context() is authority
