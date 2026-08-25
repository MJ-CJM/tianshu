"""Runtime release identity and generation lifecycle model contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tianshu.models.canonical import canonical_sha256
from tianshu.models.runtime_generation import (
    GenerationPointerV1,
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
    validate_last_good_generation_transition,
    validate_regular_generation_transition,
)

_NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _release_material() -> dict[str, object]:
    manifest = {
        "schema_version": "1",
        "manifest_id": "keqing-pi-v1",
        "capabilities": [{"capability": "pause", "state": "enforced"}],
    }
    return {
        "schema_version": 1,
        "scope": "executor:keqing:pi",
        "manifest": manifest,
        "manifest_hash": canonical_sha256(manifest),
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": "/opt/tianshu/bin/pi",
        "binary_digest": "a" * 64,
        "package_name": "@earendil-works/pi-coding-agent",
        "package_entrypoint": "dist/cli.js",
        "package_digest": "b" * 64,
        "single_argv_shape": "pi-single-v1",
        "session_argv_shape": "pi-session-v1",
        "pi_wire_version": 3,
        "materializer_id": "pi-release",
        "materializer_version": "1",
    }


def _release() -> RuntimeReleaseV1:
    material = _release_material()
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


def test_release_is_canonical_content_addressed_and_deeply_immutable() -> None:
    release = _release()

    assert release.release_digest == canonical_sha256(
        release.model_dump(mode="json", exclude={"release_digest"})
    )
    with pytest.raises(TypeError, match="immutable"):
        release.manifest["manifest_id"] = "different"
    capabilities = release.manifest["capabilities"]
    assert isinstance(capabilities, list)
    with pytest.raises(TypeError, match="immutable"):
        capabilities.append({"capability": "pause"})


@pytest.mark.parametrize(
    "mutation",
    [
        {"manifest_hash": "c" * 64},
        {"release_digest": "d" * 64},
        {"binary_path": "relative/pi"},
        {"package_name": "   "},
        {"package_entrypoint": "../dist/cli.js"},
        {"scope": "   "},
    ],
)
def test_release_rejects_incomplete_or_mismatched_material(mutation: dict[str, object]) -> None:
    material = _release_material()
    release_digest = canonical_sha256(material)
    values = {**material, "release_digest": release_digest, **mutation}

    with pytest.raises(ValidationError):
        RuntimeReleaseV1(**values)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RuntimeGenerationState.STAGED, RuntimeGenerationState.WARMING),
        (RuntimeGenerationState.STAGED, RuntimeGenerationState.FAILED),
        (RuntimeGenerationState.WARMING, RuntimeGenerationState.READY),
        (RuntimeGenerationState.WARMING, RuntimeGenerationState.FAILED),
        (RuntimeGenerationState.READY, RuntimeGenerationState.ACTIVE),
        (RuntimeGenerationState.READY, RuntimeGenerationState.FAILED),
        (RuntimeGenerationState.ACTIVE, RuntimeGenerationState.DRAINING),
        (RuntimeGenerationState.DRAINING, RuntimeGenerationState.DISPOSED),
    ],
)
def test_regular_transition_graph_accepts_every_declared_edge(
    source: RuntimeGenerationState,
    target: RuntimeGenerationState,
) -> None:
    validate_regular_generation_transition(source, target)


def test_regular_graph_rejects_rollback_but_last_good_validator_accepts_only_it() -> None:
    with pytest.raises(ValueError, match="invalid generation transition"):
        validate_regular_generation_transition(
            RuntimeGenerationState.DRAINING,
            RuntimeGenerationState.ACTIVE,
        )
    validate_last_good_generation_transition(
        RuntimeGenerationState.DRAINING,
        RuntimeGenerationState.ACTIVE,
    )
    with pytest.raises(ValueError, match="invalid last-good transition"):
        validate_last_good_generation_transition(
            RuntimeGenerationState.READY,
            RuntimeGenerationState.ACTIVE,
        )


def test_generation_requires_canonical_identity_version_and_lifecycle_times() -> None:
    release = _release()
    with pytest.raises(ValidationError):
        RuntimeGenerationV1(
            generation_id="rg-not-a-digest",
            scope=release.scope,
            release_digest=release.release_digest,
            state=RuntimeGenerationState.STAGED,
            version=1,
            created_at=_NOW,
            updated_at=_NOW,
        )
    with pytest.raises(ValidationError, match="activated_at"):
        RuntimeGenerationV1(
            generation_id="rg-" + "1" * 32,
            scope=release.scope,
            release_digest=release.release_digest,
            state=RuntimeGenerationState.ACTIVE,
            version=2,
            created_at=_NOW,
            updated_at=_NOW + timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="updated_at"):
        RuntimeGenerationV1(
            generation_id="rg-" + "1" * 32,
            scope=release.scope,
            release_digest=release.release_digest,
            state=RuntimeGenerationState.STAGED,
            version=1,
            created_at=_NOW,
            updated_at=_NOW - timedelta(seconds=1),
        )


def test_generation_pointer_is_strict_and_normalizes_timestamp_to_utc() -> None:
    pointer = GenerationPointerV1(
        scope="executor:keqing:pi",
        active_generation_id="rg-" + "1" * 32,
        last_good_generation_id="rg-" + "2" * 32,
        version=3,
        updated_at=_NOW,
    )

    assert pointer.updated_at.tzinfo is UTC
    assert pointer.version == 3
