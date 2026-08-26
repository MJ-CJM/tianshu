"""Internal orchestration for durable executor generations.

This module deliberately exposes no HTTP or CLI surface.  The controller is the
single composition point between caller-owned SQLite transactions, release
materialization and the process-local executor registry.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from tianshu.executor.adapters import (
    ExecutorAdapterRegistry,
    ExecutorGenerationConflict,
    ExecutorGenerationUnavailable,
)
from tianshu.executor.adapters.protocol import ExecutorAdapter
from tianshu.models.governance_contract import RequestedGovernanceContractV1
from tianshu.models.runtime_generation import (
    GenerationPointerV1,
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage.generation_repo import (
    GenerationActivationResult,
    GenerationRepository,
    GenerationRollbackResult,
)
from tianshu.storage.unit_of_work import SqliteUnitOfWork


def _frozen_sorted_mapping[T](values: Mapping[str, T]) -> Mapping[str, T]:
    return MappingProxyType(dict(sorted(values.items())))


@dataclass(frozen=True, slots=True)
class GenerationSelection:
    """One canonical, immutable generation snapshot reserved for an attempt."""

    generation_ids: tuple[str, ...]
    by_scope: Mapping[str, str]
    executor_manifest_digests: Mapping[str, str]
    bundles: Mapping[str, object]

    def __post_init__(self) -> None:
        by_scope = _frozen_sorted_mapping(self.by_scope)
        manifest_digests = _frozen_sorted_mapping(self.executor_manifest_digests)
        bundles = _frozen_sorted_mapping(self.bundles)
        if any(
            not isinstance(scope, str)
            or not scope.strip()
            or not isinstance(generation_id, str)
            or not generation_id.strip()
            for scope, generation_id in by_scope.items()
        ):
            raise ValueError("generation selection contains blank identity")
        expected_ids = tuple(by_scope[scope] for scope in by_scope)
        if self.generation_ids != expected_ids:
            raise ValueError("generation ids must match canonical scope order")
        if len(set(self.generation_ids)) != len(self.generation_ids):
            raise ValueError("generation selection contains duplicate ids")
        if set(bundles) != set(by_scope):
            raise ValueError("generation bundles must match selected scopes")
        if any(
            not isinstance(adapter_id, str)
            or not adapter_id.strip()
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            for adapter_id, digest in manifest_digests.items()
        ):
            raise ValueError("executor manifest digests are invalid")
        object.__setattr__(self, "by_scope", by_scope)
        object.__setattr__(self, "executor_manifest_digests", manifest_digests)
        object.__setattr__(self, "bundles", bundles)


class MaterializedGenerationBundle(Protocol):
    """Minimum materializer output retained by the generic registry."""

    @property
    def scope(self) -> str: ...

    @property
    def adapter_id(self) -> str: ...

    @property
    def release_digest(self) -> str: ...

    @property
    def manifest_content_hash(self) -> str: ...

    @property
    def executor_adapter(self) -> ExecutorAdapter: ...


class ReleaseMaterializer(Protocol):
    def materialize(self, release: RuntimeReleaseV1) -> MaterializedGenerationBundle: ...


type UnitOfWorkFactory = Callable[[], SqliteUnitOfWork]
type WarmProbe = Callable[[MaterializedGenerationBundle], Awaitable[tuple[bool, str | None]]]
type RequiredScopeProvider = Callable[[sqlite3.Connection, str], tuple[str, ...]]
type RecoveryRootProvider = Callable[[sqlite3.Connection], frozenset[str]]
type StageCommitHook = Callable[[sqlite3.Connection, RuntimeGenerationV1], None]
type ActivationCommitHook = Callable[
    [sqlite3.Connection, RuntimeGenerationV1, GenerationPointerV1 | None], None
]
type PreActivationFailureCommitHook = Callable[[sqlite3.Connection, RuntimeGenerationV1], None]


class GenerationControllerError(RuntimeError):
    """Base error for internal runtime-generation orchestration."""


class GenerationMaterializationError(GenerationControllerError):
    """Release material does not match its durable generation identity."""


class GenerationWarmError(GenerationControllerError):
    def __init__(self, generation_id: str, reason: str) -> None:
        self.generation_id = generation_id
        self.reason = reason
        super().__init__(f"generation warm failed for {generation_id}: {reason}")


class GenerationRecoveryError(GenerationControllerError):
    """Startup could not reconstruct every retained generation."""


@dataclass(frozen=True, slots=True)
class GenerationRecoveryReport:
    materialized_generation_ids: tuple[str, ...]
    failed_generation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GenerationScopeStatus:
    """Read-only projection of one active generation pointer."""

    id: str
    state: RuntimeGenerationState
    active_runs: int
    last_good_id: str


def requested_executor_scopes(
    connection: sqlite3.Connection,
    memorial_id: str,
) -> tuple[str, ...]:
    """Strictly decode the persisted requested contract for one run.

    Only Pi requests consume the Pi generation pointer.  Native and other
    Keqing executors intentionally return the legacy empty-generation path.
    """

    if not memorial_id.strip():
        raise ValueError("memorial_id must be non-blank")
    row = connection.execute(
        """
        SELECT requested.contract_json, requested.contract_hash,
               memorial.runtime_override_json
        FROM memorials AS memorial
        JOIN requested_governance_contracts AS requested
          ON requested.edict_id = memorial.edict_id
        WHERE memorial.id = ?
        """,
        (memorial_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"requested governance contract not found for {memorial_id}")
    contract = RequestedGovernanceContractV1.model_validate_json(row["contract_json"])
    if contract.content_hash != row["contract_hash"]:
        raise ValueError(f"requested governance contract hash mismatch for {memorial_id}")
    adapter_id = contract.executor.adapter_id
    raw_override = row["runtime_override_json"]
    if raw_override is not None:
        try:
            runtime_override = json.loads(raw_override)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"runtime override is invalid for {memorial_id}") from exc
        if not isinstance(runtime_override, dict):
            raise ValueError(f"runtime override must be an object for {memorial_id}")
        if "executor" in runtime_override:
            override = runtime_override["executor"]
            if not isinstance(override, str) or not override.strip():
                raise ValueError(f"runtime executor override is invalid for {memorial_id}")
            adapter_id = override
    if adapter_id == "keqing:pi":
        return ("executor:keqing:pi",)
    return ()


class GenerationController:
    """Own stage/warm/activation/recovery and exact-attempt selection."""

    def __init__(
        self,
        repository: GenerationRepository,
        unit_of_work_factory: UnitOfWorkFactory,
        materializer: ReleaseMaterializer,
        registry: ExecutorAdapterRegistry,
        *,
        warm_probe: WarmProbe,
        required_scope_provider: RequiredScopeProvider = requested_executor_scopes,
        recovery_root_provider: RecoveryRootProvider | None = None,
        managed_scopes: tuple[str, ...] | None = None,
        recovery_scopes: tuple[str, ...] | None = None,
        generation_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._unit_of_work_factory = unit_of_work_factory
        self._materializer = materializer
        self._registry = registry
        self._warm_probe = warm_probe
        self._required_scope_provider = required_scope_provider
        self._recovery_root_provider = recovery_root_provider or (lambda _connection: frozenset())
        self._managed_scopes = self._scope_set(managed_scopes, field="managed_scopes")
        self._recovery_scopes = self._scope_set(
            recovery_scopes if recovery_scopes is not None else managed_scopes,
            field="recovery_scopes",
        )
        if (
            self._managed_scopes is not None
            and self._recovery_scopes is not None
            and not self._recovery_scopes.issubset(self._managed_scopes)
        ):
            raise ValueError("recovery_scopes must be a subset of managed_scopes")
        self._generation_id_factory = generation_id_factory or (lambda: f"rg-{uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))

    def stage(self, release: RuntimeReleaseV1) -> RuntimeGenerationV1:
        """Materialize outside SQLite, then durably stage and publish the bundle."""

        return self.stage_exact(release, generation_id=self._generation_id_factory())

    def stage_exact(
        self,
        release: RuntimeReleaseV1,
        *,
        generation_id: str,
        stage_commit_hook: StageCommitHook | None = None,
    ) -> RuntimeGenerationV1:
        """Stage one deterministic generation, accepting only an exact replay.

        ``stage_commit_hook`` lets a caller persist an external authority in the
        same transaction as the STAGED row.  The hook must use the supplied
        connection and must not commit it.
        """

        self._require_managed_scope(release.scope)
        bundle = self._materializer.materialize(release)
        self._validate_bundle(release, bundle)
        now = self._now()
        committed = False
        inserted = False
        try:
            with (
                self._unit_of_work_factory() as unit_of_work,
                self._registry.generation_guard(),
            ):
                existing = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=release.scope,
                    generation_id=generation_id,
                )
                if existing is not None:
                    durable_release = self._repository.get_release(
                        unit_of_work.connection,
                        scope=release.scope,
                        release_digest=existing.release_digest,
                    )
                    if (
                        durable_release != release
                        or existing.release_digest != release.release_digest
                        or existing.state
                        not in {
                            RuntimeGenerationState.STAGED,
                            RuntimeGenerationState.WARMING,
                            RuntimeGenerationState.READY,
                        }
                    ):
                        raise GenerationControllerError(
                            "deterministic generation identity conflicts with durable state"
                        )
                    if stage_commit_hook is not None:
                        stage_commit_hook(unit_of_work.connection, existing)
                    unit_of_work.commit()
                    committed = True
                    self._reconcile_bundle(existing, bundle)
                    return existing
                generation = RuntimeGenerationV1(
                    generation_id=generation_id,
                    scope=release.scope,
                    release_digest=release.release_digest,
                    state=RuntimeGenerationState.STAGED,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                self._repository.insert_release(
                    unit_of_work.connection,
                    release,
                    first_seen_at=now,
                )
                durable = self._repository.insert_staged(
                    unit_of_work.connection,
                    generation,
                )
                if stage_commit_hook is not None:
                    stage_commit_hook(unit_of_work.connection, durable)
                unit_of_work.commit()
                committed = True
                inserted = True
                self._install_bundle(durable, bundle)
        except Exception:
            if committed and inserted:
                self._fail_unpublished_stage(durable)
            raise
        return durable

    async def warm_or_resume(self, generation_id: str) -> RuntimeGenerationV1:
        """Warm a staged generation or resume an exact interrupted preparation."""

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            current = self._require_materialized_generation_current(
                unit_of_work.connection,
                generation_id,
                expected_states={
                    RuntimeGenerationState.STAGED,
                    RuntimeGenerationState.WARMING,
                    RuntimeGenerationState.READY,
                },
            )
            release = self._repository.get_release(
                unit_of_work.connection,
                scope=current.scope,
                release_digest=current.release_digest,
            )
            if release is None:
                raise GenerationMaterializationError(
                    f"warm release does not exist: {current.generation_id}"
                )
            unit_of_work.commit()

        if current.state is RuntimeGenerationState.STAGED:
            return await self.warm(generation_id)

        try:
            bundle = await asyncio.to_thread(self._materializer.materialize, release)
            self._validate_bundle(release, bundle)
        except asyncio.CancelledError:
            if current.state is RuntimeGenerationState.WARMING:
                self._fail_interrupted_warm(current)
            raise
        except Exception as exc:
            if current.state is RuntimeGenerationState.WARMING:
                self._fail_interrupted_warm(current)
            raise GenerationWarmError(generation_id, "material_verification_failed") from exc

        if current.state is RuntimeGenerationState.READY:
            with self._registry.generation_guard():
                self._reconcile_bundle(current, bundle)
            return current
        return await self._probe_warming(current, bundle)

    async def warm(self, generation_id: str) -> RuntimeGenerationV1:
        """Reverify one staged release, then probe and publish it as ready."""

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            staged = self._require_materialized_generation_current(
                unit_of_work.connection,
                generation_id,
                expected_states={RuntimeGenerationState.STAGED},
            )
            release = self._repository.get_release(
                unit_of_work.connection,
                scope=staged.scope,
                release_digest=staged.release_digest,
            )
            if release is None:
                raise GenerationMaterializationError(
                    f"warm release does not exist: {staged.generation_id}"
                )
            unit_of_work.commit()

        try:
            bundle = await asyncio.to_thread(self._materializer.materialize, release)
            self._validate_bundle(release, bundle)
        except asyncio.CancelledError:
            self._fail_warm_materialization(staged)
            raise
        except Exception as exc:
            self._fail_warm_materialization(staged)
            raise GenerationWarmError(generation_id, "material_verification_failed") from exc

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            current = self._require_materialized_generation_current(
                unit_of_work.connection,
                generation_id,
                expected_states={RuntimeGenerationState.STAGED},
            )
            if current != staged:
                raise GenerationControllerError(
                    f"generation changed while validating warm material: {generation_id}"
                )
            warming = self._repository.transition_pre_activation(
                unit_of_work.connection,
                scope=staged.scope,
                generation_id=staged.generation_id,
                target_state=RuntimeGenerationState.WARMING,
                expected_version=staged.version,
                updated_at=self._now(),
            )
            unit_of_work.commit()
            self._publish_committed_state(unit_of_work.connection, warming)

        return await self._probe_warming(warming, bundle)

    def activate(
        self,
        generation_id: str,
        *,
        expected_active_generation_id: str | None = None,
        expected_active_release_digest: str | None = None,
        activation_commit_hook: ActivationCommitHook | None = None,
    ) -> GenerationActivationResult:
        """Revalidate release bytes, atomically switch, then publish committed states."""

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            target = self._require_materialized_generation_current(
                unit_of_work.connection,
                generation_id,
                expected_states={RuntimeGenerationState.READY},
            )
            pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=target.scope,
            )
            if expected_active_generation_id is not None and (
                pointer is None or pointer.active_generation_id != expected_active_generation_id
            ):
                raise GenerationControllerError(
                    "generation active pointer does not match activation authority"
                )
            if expected_active_release_digest is not None:
                if pointer is None:
                    raise GenerationControllerError(
                        "generation active pointer does not match activation authority"
                    )
                active = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=target.scope,
                    generation_id=pointer.active_generation_id,
                )
                if active is None or active.release_digest != expected_active_release_digest:
                    raise GenerationControllerError(
                        "generation active release does not match activation authority"
                    )
            if pointer is not None:
                self._require_registry_identity(
                    unit_of_work.connection,
                    scope=target.scope,
                    generation_id=pointer.active_generation_id,
                )
            release = self._repository.get_release(
                unit_of_work.connection,
                scope=target.scope,
                release_digest=target.release_digest,
            )
            if release is None:
                raise GenerationMaterializationError(
                    f"activation release does not exist: {target.generation_id}"
                )
            unit_of_work.commit()

        try:
            self._validate_bundle(release, self._materializer.materialize(release))
            # Confirm again after the first verifier has returned.  This catches
            # material swapped by a verifier wrapper or concurrent publisher at
            # the original validate-to-CAS boundary without doing filesystem I/O
            # while SQLite owns a write transaction.
            self._validate_bundle(release, self._materializer.materialize(release))
        except Exception as exc:
            raise GenerationMaterializationError(
                f"activation generation material is unavailable: {target.generation_id}"
            ) from exc

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            current = self._repository.get_generation(
                unit_of_work.connection,
                scope=target.scope,
                generation_id=target.generation_id,
            )
            current_pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=target.scope,
            )
            if current != target or current_pointer != pointer:
                raise GenerationControllerError(
                    f"generation changed while validating activation: {generation_id}"
                )
            self._require_registry_identity(
                unit_of_work.connection,
                scope=target.scope,
                generation_id=target.generation_id,
            )
            if current_pointer is not None:
                self._require_registry_identity(
                    unit_of_work.connection,
                    scope=target.scope,
                    generation_id=current_pointer.active_generation_id,
                )
            if activation_commit_hook is not None:
                activation_commit_hook(unit_of_work.connection, target, current_pointer)
            result = self._repository.activate(
                unit_of_work.connection,
                scope=target.scope,
                target_generation_id=target.generation_id,
                expected_generation_version=target.version,
                expected_pointer_version=(
                    current_pointer.version if current_pointer is not None else None
                ),
                updated_at=self._now(),
            )
            unit_of_work.commit()
            if result.draining is not None:
                self._publish_committed_state(unit_of_work.connection, result.draining)
            self._publish_committed_state(unit_of_work.connection, result.activated)
            return result

    def activate_exact(
        self,
        generation_id: str,
        *,
        expected_active_generation_id: str,
        expected_active_release_digest: str,
        activation_commit_hook: ActivationCommitHook | None = None,
    ) -> GenerationActivationResult:
        """Activate one authorized READY generation or accept its exact replay."""

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            retained = self._registry.generation_record(generation_id)
            if retained is None:
                raise GenerationMaterializationError(
                    f"generation bundle is not materialized: {generation_id}"
                )
            self._require_managed_scope(retained.scope)
            target = self._repository.get_generation(
                unit_of_work.connection,
                scope=retained.scope,
                generation_id=generation_id,
            )
            pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=retained.scope,
            )
            if (
                target is not None
                and target.state is RuntimeGenerationState.ACTIVE
                and pointer is not None
                and pointer.active_generation_id == generation_id
                and pointer.last_good_generation_id == expected_active_generation_id
            ):
                draining = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=retained.scope,
                    generation_id=expected_active_generation_id,
                )
                if (
                    draining is None
                    or draining.state is not RuntimeGenerationState.DRAINING
                    or draining.release_digest != expected_active_release_digest
                ):
                    raise GenerationControllerError(
                        "completed activation does not match activation authority"
                    )
                if activation_commit_hook is not None:
                    activation_commit_hook(unit_of_work.connection, target, pointer)
                unit_of_work.commit()
                return GenerationActivationResult(
                    pointer=pointer,
                    activated=target,
                    draining=draining,
                )
            unit_of_work.commit()
        return self.activate(
            generation_id,
            expected_active_generation_id=expected_active_generation_id,
            expected_active_release_digest=expected_active_release_digest,
            activation_commit_hook=activation_commit_hook,
        )

    def fail_pre_active_exact(
        self,
        scope: str,
        *,
        generation_id: str,
        expected_release_digest: str,
        failure_commit_hook: PreActivationFailureCommitHook | None = None,
    ) -> RuntimeGenerationV1:
        """Fail one exact pre-active generation and atomically withdraw its authority."""

        self._require_managed_scope(scope)
        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            current = self._repository.get_generation(
                unit_of_work.connection,
                scope=scope,
                generation_id=generation_id,
            )
            if current is None or current.release_digest != expected_release_digest:
                raise GenerationControllerError(
                    "pre-active generation does not match failure authority"
                )
            if current.state is RuntimeGenerationState.FAILED:
                failed = current
            elif current.state in {
                RuntimeGenerationState.STAGED,
                RuntimeGenerationState.WARMING,
                RuntimeGenerationState.READY,
            }:
                failed = self._repository.transition_pre_activation(
                    unit_of_work.connection,
                    scope=scope,
                    generation_id=generation_id,
                    target_state=RuntimeGenerationState.FAILED,
                    expected_version=current.version,
                    updated_at=self._now(),
                )
            else:
                raise GenerationControllerError(
                    "only a pre-active generation can be failed by preparation compensation"
                )
            if failure_commit_hook is not None:
                failure_commit_hook(unit_of_work.connection, failed)
            unit_of_work.commit()
            self._remove_terminal_bundle(unit_of_work.connection, failed)
            return failed

    def rollback(
        self,
        scope: str,
        *,
        expected_active_generation_id: str | None = None,
        expected_last_good_generation_id: str | None = None,
    ) -> GenerationRollbackResult:
        """Atomically reactivate the CAS-protected last-good generation."""

        if not scope.strip():
            raise ValueError("scope must be non-blank")
        self._require_managed_scope(scope)
        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            expected_pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=scope,
            )
            if expected_pointer is None:
                raise GenerationControllerError(f"generation pointer does not exist for {scope}")
            if (
                expected_active_generation_id is not None
                and expected_pointer.active_generation_id != expected_active_generation_id
            ):
                raise GenerationControllerError(
                    "generation active pointer does not match rollback authority"
                )
            if (
                expected_last_good_generation_id is not None
                and expected_pointer.last_good_generation_id != expected_last_good_generation_id
            ):
                raise GenerationControllerError(
                    "generation last-good pointer does not match rollback authority"
                )
            last_good = self._require_registry_identity(
                unit_of_work.connection,
                scope=scope,
                generation_id=expected_pointer.last_good_generation_id,
            )
            release = self._repository.get_release(
                unit_of_work.connection,
                scope=scope,
                release_digest=last_good.release_digest,
            )
            if release is None:
                raise GenerationMaterializationError(
                    f"last-good release does not exist: {last_good.generation_id}"
                )
            unit_of_work.commit()

        try:
            self._validate_bundle(release, self._materializer.materialize(release))
            # Confirm again after the first verifier has returned. This closes
            # the same verifier-return drift window guarded by ``activate``
            # without performing filesystem I/O inside a SQLite transaction.
            self._validate_bundle(release, self._materializer.materialize(release))
        except Exception as exc:
            raise GenerationMaterializationError(
                f"last-good generation material is unavailable: {last_good.generation_id}"
            ) from exc

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=scope,
            )
            if pointer != expected_pointer:
                raise GenerationControllerError(
                    f"generation pointer changed while validating rollback for {scope}"
                )
            self._require_registry_identity(
                unit_of_work.connection,
                scope=scope,
                generation_id=pointer.active_generation_id,
            )
            self._require_registry_identity(
                unit_of_work.connection,
                scope=scope,
                generation_id=pointer.last_good_generation_id,
            )
            result = self._repository.rollback_to_last_good(
                unit_of_work.connection,
                scope=scope,
                expected_pointer_version=pointer.version,
                updated_at=self._now(),
            )
            unit_of_work.commit()
            self._publish_committed_state(unit_of_work.connection, result.draining)
            self._publish_committed_state(unit_of_work.connection, result.activated)
            return result

    def rollback_exact(
        self,
        scope: str,
        *,
        expected_active_generation_id: str,
        expected_last_good_generation_id: str,
    ) -> GenerationRollbackResult:
        """Rollback an exact authority pair or accept its completed replay."""

        self._require_managed_scope(scope)
        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            pointer = self._repository.get_pointer(unit_of_work.connection, scope=scope)
            if (
                pointer is not None
                and pointer.active_generation_id == expected_last_good_generation_id
                and pointer.last_good_generation_id == expected_last_good_generation_id
            ):
                activated = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=scope,
                    generation_id=expected_last_good_generation_id,
                )
                draining = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=scope,
                    generation_id=expected_active_generation_id,
                )
                if (
                    activated is None
                    or activated.state is not RuntimeGenerationState.ACTIVE
                    or draining is None
                    or draining.state is not RuntimeGenerationState.DRAINING
                ):
                    raise GenerationControllerError(
                        "completed rollback does not match rollback authority"
                    )
                unit_of_work.commit()
                return GenerationRollbackResult(
                    pointer=pointer,
                    activated=activated,
                    draining=draining,
                )
            unit_of_work.commit()
        return self.rollback(
            scope,
            expected_active_generation_id=expected_active_generation_id,
            expected_last_good_generation_id=expected_last_good_generation_id,
        )

    def recover(
        self,
        *,
        pre_active_root_ids: frozenset[str] = frozenset(),
    ) -> GenerationRecoveryReport:
        """Fail abandoned pre-active rows and rehydrate every retained bundle."""

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            if self._registry.attempt_leases():
                raise GenerationRecoveryError("recovery requires an empty attempt lease map")
            terminal: list[RuntimeGenerationV1] = []
            for record in self._registry.generation_records():
                if not self._recovers_scope(record.scope):
                    continue
                generation = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=record.scope,
                    generation_id=record.generation_id,
                )
                if generation is None:
                    raise GenerationRecoveryError(
                        f"materialized generation has no durable identity: {record.generation_id}"
                    )
                if generation.state in {
                    RuntimeGenerationState.FAILED,
                    RuntimeGenerationState.DISPOSED,
                }:
                    self._require_registry_identity(
                        unit_of_work.connection,
                        scope=generation.scope,
                        generation_id=generation.generation_id,
                        durable=generation,
                        include_state=False,
                    )
                    terminal.append(generation)
            candidates = self._recovery_candidates(unit_of_work.connection)
            authority_root_ids = self._recovery_root_provider(unit_of_work.connection)
            candidate_ids = frozenset(item.generation_id for item in candidates)
            if not authority_root_ids.issubset(candidate_ids):
                raise GenerationRecoveryError(
                    "executor generation authority references an unavailable generation"
                )
            recovery_root_ids = authority_root_ids | pre_active_root_ids
            if self._recovery_scopes is None:
                durable_retained_ids = self._repository.retained_generation_ids(
                    unit_of_work.connection
                )
            else:
                durable_retained_ids = frozenset().union(
                    *(
                        self._repository.retained_generation_ids(
                            unit_of_work.connection,
                            scope=scope,
                        )
                        for scope in sorted(self._recovery_scopes)
                    )
                )
            retained_ids = durable_retained_ids | authority_root_ids
            failed: list[RuntimeGenerationV1] = []
            retained: list[tuple[RuntimeGenerationV1, RuntimeReleaseV1]] = []
            for generation in candidates:
                if generation.state in {
                    RuntimeGenerationState.STAGED,
                    RuntimeGenerationState.WARMING,
                    RuntimeGenerationState.READY,
                }:
                    if generation.generation_id in recovery_root_ids:
                        release = self._repository.get_release(
                            unit_of_work.connection,
                            scope=generation.scope,
                            release_digest=generation.release_digest,
                        )
                        if release is None:
                            raise GenerationRecoveryError(
                                f"release missing for generation {generation.generation_id}"
                            )
                        retained.append((generation, release))
                    else:
                        failed.append(
                            self._repository.transition_pre_activation(
                                unit_of_work.connection,
                                scope=generation.scope,
                                generation_id=generation.generation_id,
                                target_state=RuntimeGenerationState.FAILED,
                                expected_version=generation.version,
                                updated_at=self._now(),
                            )
                        )
                    continue
                if generation.generation_id not in retained_ids:
                    if generation.state is RuntimeGenerationState.ACTIVE:
                        raise GenerationRecoveryError(
                            f"active generation is not durably retained: {generation.generation_id}"
                        )
                    # An unreferenced draining generation needs no executable
                    # material.  The first reconciler tick will durably dispose
                    # it, so stale historical package bytes cannot block startup.
                    continue
                release = self._repository.get_release(
                    unit_of_work.connection,
                    scope=generation.scope,
                    release_digest=generation.release_digest,
                )
                if release is None:
                    raise GenerationRecoveryError(
                        f"release missing for generation {generation.generation_id}"
                    )
                retained.append((generation, release))
            unit_of_work.commit()

            for generation in terminal:
                self._remove_terminal_bundle(unit_of_work.connection, generation)
            for generation in failed:
                if self._registry.generation_record(generation.generation_id) is not None:
                    self._remove_terminal_bundle(unit_of_work.connection, generation)

        materialized: list[tuple[RuntimeGenerationV1, MaterializedGenerationBundle]] = []
        try:
            for generation, release in retained:
                bundle = self._materializer.materialize(release)
                self._validate_bundle(release, bundle)
                materialized.append((generation, bundle))
        except Exception as exc:
            raise GenerationRecoveryError("retained generation materialization failed") from exc

        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            for generation, _bundle in materialized:
                current = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=generation.scope,
                    generation_id=generation.generation_id,
                )
                if current != generation:
                    raise GenerationRecoveryError(
                        f"generation changed during recovery: {generation.generation_id}"
                    )
            unit_of_work.commit()
            for generation, bundle in materialized:
                self._reconcile_bundle(generation, bundle)
        return GenerationRecoveryReport(
            materialized_generation_ids=tuple(
                generation.generation_id for generation, _ in materialized
            ),
            failed_generation_ids=tuple(generation.generation_id for generation in failed),
        )

    def resolve_for_binding(
        self,
        memorial_id: str,
        attempt_id: str,
        *,
        pinned_ids: tuple[str, ...] = (),
        inherit_pinned: bool = False,
        allow_ready: bool = False,
    ) -> GenerationSelection:
        """Convenience wrapper that owns a short read transaction."""

        with self._unit_of_work_factory() as unit_of_work:
            selection = self.resolve_for_binding_current(
                unit_of_work.connection,
                memorial_id,
                attempt_id,
                pinned_ids=pinned_ids,
                inherit_pinned=inherit_pinned,
                allow_ready=allow_ready,
            )
            unit_of_work.commit()
            return selection

    def resolve_for_binding_current(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
        attempt_id: str,
        *,
        pinned_ids: tuple[str, ...] = (),
        inherit_pinned: bool = False,
        allow_ready: bool = False,
    ) -> GenerationSelection:
        """Resolve inside a caller-owned UoW without opening a nested transaction."""

        if not connection.in_transaction:
            raise RuntimeError("generation binding requires a caller-owned transaction")
        with self._registry.generation_guard():
            required_scopes = self._canonical_required_scopes(
                self._required_scope_provider(connection, memorial_id)
            )
            for scope in required_scopes:
                self._require_managed_scope(scope)
            # Continuity pins apply only while the child/retry still requires a
            # generated scope.  An explicit executor override to a legacy/static
            # backend must not turn the parent's Pi generation into a false
            # ``generation_retired`` failure.
            effective_pins = () if inherit_pinned and not required_scopes else pinned_ids
            selected_ids = effective_pins or self._active_generation_ids(
                connection, required_scopes
            )
            self.validate_selection(
                connection,
                selected_ids,
                required_scopes=required_scopes,
                allow_ready=allow_ready,
            )
            selection = self._registry.reserve_binding(
                attempt_id,
                pinned_ids=selected_ids,
                required_scopes=required_scopes,
                allow_ready=allow_ready,
            )
            return selection

    def validate_selection(
        self,
        connection: sqlite3.Connection,
        generation_ids: tuple[str, ...],
        *,
        required_scopes: tuple[str, ...],
        allow_ready: bool = False,
    ) -> tuple[RuntimeGenerationV1, ...]:
        """Validate durable and materialized identities without live fallback."""

        scopes = self._canonical_required_scopes(required_scopes)
        for scope in scopes:
            self._require_managed_scope(scope)
        if not generation_ids:
            return ()
        generations = self._repository.validate_generation_ids(
            connection,
            generation_ids,
            expected_scopes=scopes,
        )
        allowed_states = {
            RuntimeGenerationState.ACTIVE,
            RuntimeGenerationState.DRAINING,
        }
        if allow_ready:
            allowed_states.add(RuntimeGenerationState.READY)
        for generation in generations:
            if generation.state not in allowed_states:
                raise ExecutorGenerationUnavailable(
                    f"generation {generation.generation_id!r} is unavailable in state "
                    f"{generation.state.value!r}"
                )
            self._require_registry_identity(
                connection,
                scope=generation.scope,
                generation_id=generation.generation_id,
                durable=generation,
            )
        return generations

    def release_binding(self, attempt_id: str) -> bool:
        return self._registry.release(attempt_id)

    def status_for_scope(self, scope: str) -> GenerationScopeStatus | None:
        """Read one pointer and its process-local lease count without side effects."""

        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("scope must be non-blank")
        self._require_managed_scope(scope)
        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=scope,
            )
            if pointer is None:
                unit_of_work.commit()
                return None
            active = self._require_registry_identity(
                unit_of_work.connection,
                scope=scope,
                generation_id=pointer.active_generation_id,
            )
            active_runs = self._registry.active_attempt_count(active.generation_id)
            unit_of_work.commit()
            return GenerationScopeStatus(
                id=active.generation_id,
                state=active.state,
                active_runs=active_runs,
                last_good_id=pointer.last_good_generation_id,
            )

    def _active_generation_ids(
        self,
        connection: sqlite3.Connection,
        required_scopes: tuple[str, ...],
    ) -> tuple[str, ...]:
        pointers = tuple(
            self._repository.get_pointer(connection, scope=scope) for scope in required_scopes
        )
        if not pointers or all(pointer is None for pointer in pointers):
            return ()
        if any(pointer is None for pointer in pointers):
            raise ExecutorGenerationUnavailable(
                "required generation scopes have a partial pointer set"
            )
        return tuple(pointer.active_generation_id for pointer in pointers if pointer is not None)

    def _require_materialized_generation_current(
        self,
        connection: sqlite3.Connection,
        generation_id: str,
        *,
        expected_states: set[RuntimeGenerationState],
    ) -> RuntimeGenerationV1:
        retained = self._registry.generation_record(generation_id)
        if retained is None:
            raise GenerationMaterializationError(
                f"generation bundle is not materialized: {generation_id}"
            )
        self._require_managed_scope(retained.scope)
        generation = self._repository.get_generation(
            connection,
            scope=retained.scope,
            generation_id=generation_id,
        )
        if generation is None:
            raise GenerationControllerError(f"generation does not exist: {generation_id}")
        if generation.state not in expected_states:
            raise GenerationControllerError(
                f"generation {generation_id} is in state {generation.state.value}"
            )
        if retained.state != generation.state.value:
            raise GenerationMaterializationError("durable and materialized states disagree")
        if retained.release_digest != generation.release_digest:
            raise GenerationMaterializationError("durable and materialized releases disagree")
        release = self._repository.get_release(
            connection,
            scope=generation.scope,
            release_digest=generation.release_digest,
        )
        if release is None or retained.executor_manifest_digests != (
            (retained.adapter.adapter_id, release.manifest_hash),
        ):
            raise GenerationMaterializationError(
                "durable and materialized executor manifests disagree"
            )
        return generation

    def _require_registry_identity(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        generation_id: str,
        durable: RuntimeGenerationV1 | None = None,
        include_state: bool = True,
    ) -> RuntimeGenerationV1:
        self._require_managed_scope(scope)
        generation = durable or self._repository.get_generation(
            connection,
            scope=scope,
            generation_id=generation_id,
        )
        if generation is None:
            raise ExecutorGenerationUnavailable(f"generation does not exist: {generation_id}")
        retained = self._registry.generation_record(generation_id)
        if retained is None:
            raise ExecutorGenerationUnavailable(
                f"generation bundle is not materialized: {generation_id}"
            )
        release = self._repository.get_release(
            connection,
            scope=generation.scope,
            release_digest=generation.release_digest,
        )
        if release is None:
            raise ExecutorGenerationUnavailable(
                f"generation release is not durable: {generation_id}"
            )
        if (
            retained.scope != generation.scope
            or retained.release_digest != generation.release_digest
            or (include_state and retained.state != generation.state.value)
            or retained.executor_manifest_digests
            != ((retained.adapter.adapter_id, release.manifest_hash),)
        ):
            raise ExecutorGenerationConflict(
                "durable and materialized generation identities disagree"
            )
        return generation

    def _install_bundle(
        self,
        generation: RuntimeGenerationV1,
        bundle: MaterializedGenerationBundle,
    ) -> None:
        self._registry.install_generation(
            generation_id=generation.generation_id,
            scope=generation.scope,
            release_digest=generation.release_digest,
            state=generation.state.value,
            adapter=bundle.executor_adapter,
            bundle=bundle,
            executor_manifest_digests={
                bundle.adapter_id: bundle.manifest_content_hash,
            },
        )

    def _reconcile_bundle(
        self,
        generation: RuntimeGenerationV1,
        bundle: MaterializedGenerationBundle,
    ) -> None:
        self._registry.reconcile_generation(
            generation_id=generation.generation_id,
            scope=generation.scope,
            release_digest=generation.release_digest,
            state=generation.state.value,
            adapter=bundle.executor_adapter,
            bundle=bundle,
            executor_manifest_digests={
                bundle.adapter_id: bundle.manifest_content_hash,
            },
        )

    def _publish_committed_state(
        self,
        connection: sqlite3.Connection,
        generation: RuntimeGenerationV1,
    ) -> None:
        release = self._repository.get_release(
            connection,
            scope=generation.scope,
            release_digest=generation.release_digest,
        )
        if release is None:
            raise GenerationMaterializationError(
                f"committed generation release disappeared: {generation.generation_id}"
            )
        first_error: Exception | None = None
        for _attempt in range(2):
            try:
                self._registry.update_generation_state(
                    generation.generation_id,
                    generation.state.value,
                )
                retained = self._registry.generation_record(generation.generation_id)
                if retained is None or (
                    retained.scope != generation.scope
                    or retained.release_digest != generation.release_digest
                    or retained.state != generation.state.value
                    or retained.executor_manifest_digests
                    != ((retained.adapter.adapter_id, release.manifest_hash),)
                ):
                    raise ExecutorGenerationConflict(
                        "published registry identity does not match durable truth"
                    )
                return
            except Exception as exc:  # registry publication must converge or fail closed
                first_error = first_error or exc

        retained = self._registry.generation_record(generation.generation_id)
        if retained is None:
            raise GenerationMaterializationError(
                f"committed generation bundle disappeared: {generation.generation_id}"
            ) from first_error
        try:
            self._registry.reconcile_generation_state(
                generation.generation_id,
                generation.state.value,
                expected_scope=generation.scope,
                expected_release_digest=generation.release_digest,
                expected_manifest_digests={
                    retained.adapter.adapter_id: release.manifest_hash,
                },
            )
        except Exception as exc:
            raise GenerationMaterializationError(
                f"committed generation registry state did not converge: {generation.generation_id}"
            ) from exc

    def _remove_terminal_bundle(
        self,
        connection: sqlite3.Connection,
        generation: RuntimeGenerationV1,
    ) -> None:
        self._publish_committed_state(connection, generation)
        first_error: Exception | None = None
        for _attempt in range(2):
            try:
                self._registry.remove_generation(generation.generation_id)
                return
            except Exception as exc:  # idempotent retry handles fail-after-remove faults
                first_error = first_error or exc
        raise GenerationMaterializationError(
            f"terminal generation bundle could not be removed: {generation.generation_id}"
        ) from first_error

    @staticmethod
    def _validate_bundle(
        release: RuntimeReleaseV1,
        bundle: MaterializedGenerationBundle,
    ) -> None:
        if (
            bundle.scope != release.scope
            or bundle.release_digest != release.release_digest
            or bundle.manifest_content_hash != release.manifest_hash
            or bundle.executor_adapter.adapter_id != bundle.adapter_id
            or bundle.executor_adapter.manifest.content_hash != bundle.manifest_content_hash
        ):
            raise GenerationMaterializationError(
                "materialized bundle does not match its persisted release"
            )

    def _fail_unpublished_stage(self, generation: RuntimeGenerationV1) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            self._repository.transition_pre_activation(
                unit_of_work.connection,
                scope=generation.scope,
                generation_id=generation.generation_id,
                target_state=RuntimeGenerationState.FAILED,
                expected_version=generation.version,
                updated_at=self._now(),
            )
            unit_of_work.commit()

    def _fail_warm_materialization(self, staged: RuntimeGenerationV1) -> None:
        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            current = self._require_materialized_generation_current(
                unit_of_work.connection,
                staged.generation_id,
                expected_states={RuntimeGenerationState.STAGED},
            )
            if current != staged:
                raise GenerationControllerError(
                    f"generation changed while validating warm material: {staged.generation_id}"
                )
            failed = self._repository.transition_pre_activation(
                unit_of_work.connection,
                scope=staged.scope,
                generation_id=staged.generation_id,
                target_state=RuntimeGenerationState.FAILED,
                expected_version=staged.version,
                updated_at=self._now(),
            )
            unit_of_work.commit()
            self._remove_terminal_bundle(unit_of_work.connection, failed)

    def _fail_interrupted_warm(self, warming: RuntimeGenerationV1) -> None:
        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            current = self._require_materialized_generation_current(
                unit_of_work.connection,
                warming.generation_id,
                expected_states={RuntimeGenerationState.WARMING},
            )
            if current != warming:
                raise GenerationControllerError(
                    f"generation changed while resuming warm: {warming.generation_id}"
                )
            failed = self._repository.transition_pre_activation(
                unit_of_work.connection,
                scope=warming.scope,
                generation_id=warming.generation_id,
                target_state=RuntimeGenerationState.FAILED,
                expected_version=warming.version,
                updated_at=self._now(),
            )
            unit_of_work.commit()
            self._remove_terminal_bundle(unit_of_work.connection, failed)

    async def _probe_warming(
        self,
        warming: RuntimeGenerationV1,
        bundle: MaterializedGenerationBundle,
    ) -> RuntimeGenerationV1:
        probe_error: BaseException | None = None
        try:
            ok, reason = await self._warm_probe(bundle)
        except asyncio.CancelledError as exc:
            ok = False
            reason = "probe_cancelled"
            probe_error = exc
        except Exception as exc:  # probe failures are durable lifecycle failures
            ok = False
            reason = f"probe_error:{type(exc).__name__}"
            probe_error = exc

        target = RuntimeGenerationState.READY if ok else RuntimeGenerationState.FAILED
        with (
            self._unit_of_work_factory() as unit_of_work,
            self._registry.generation_guard(),
        ):
            current = self._require_materialized_generation_current(
                unit_of_work.connection,
                warming.generation_id,
                expected_states={RuntimeGenerationState.WARMING},
            )
            if current != warming:
                raise GenerationControllerError(
                    f"generation changed while warming: {warming.generation_id}"
                )
            completed = self._repository.transition_pre_activation(
                unit_of_work.connection,
                scope=warming.scope,
                generation_id=warming.generation_id,
                target_state=target,
                expected_version=warming.version,
                updated_at=self._now(),
            )
            unit_of_work.commit()
            self._publish_committed_state(unit_of_work.connection, completed)
            if target is RuntimeGenerationState.FAILED:
                self._remove_terminal_bundle(unit_of_work.connection, completed)
        if target is RuntimeGenerationState.FAILED:
            if isinstance(probe_error, asyncio.CancelledError):
                raise probe_error
            error = GenerationWarmError(warming.generation_id, reason or "probe_rejected")
            if probe_error is not None:
                raise error from probe_error
            raise error
        return completed

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)

    @staticmethod
    def _scope_set(
        scopes: tuple[str, ...] | None,
        *,
        field: str,
    ) -> frozenset[str] | None:
        if scopes is None:
            return None
        if not isinstance(scopes, tuple) or any(not isinstance(scope, str) for scope in scopes):
            raise TypeError(f"{field} must be a tuple of strings")
        if any(not scope.strip() for scope in scopes):
            raise ValueError(f"{field} must contain only non-blank scopes")
        if len(scopes) != len(set(scopes)):
            raise ValueError(f"{field} must contain unique scopes")
        return frozenset(scopes)

    def _require_managed_scope(self, scope: str) -> None:
        if self._managed_scopes is not None and scope not in self._managed_scopes:
            raise GenerationControllerError(f"generation scope is not managed: {scope}")

    def _recovers_scope(self, scope: str) -> bool:
        return self._recovery_scopes is None or scope in self._recovery_scopes

    def _recovery_candidates(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[RuntimeGenerationV1, ...]:
        if self._recovery_scopes is None:
            return self._repository.list_recovery_candidates(connection)
        return tuple(
            generation
            for scope in sorted(self._recovery_scopes)
            for generation in self._repository.list_recovery_candidates(
                connection,
                scope=scope,
            )
        )

    @staticmethod
    def _canonical_required_scopes(scopes: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(scopes, tuple) or any(
            not isinstance(scope, str) or not scope.strip() for scope in scopes
        ):
            raise TypeError("required scopes must be a tuple of non-blank strings")
        if len(scopes) != len(set(scopes)):
            raise ExecutorGenerationConflict("required scopes contain duplicates")
        return tuple(sorted(scopes))


__all__ = [
    "GenerationController",
    "GenerationControllerError",
    "GenerationMaterializationError",
    "GenerationRecoveryError",
    "GenerationRecoveryReport",
    "GenerationScopeStatus",
    "GenerationSelection",
    "GenerationWarmError",
    "MaterializedGenerationBundle",
    "ReleaseMaterializer",
    "requested_executor_scopes",
]
