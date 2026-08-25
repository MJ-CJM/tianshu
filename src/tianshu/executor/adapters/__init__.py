"""Registry that resolves governance before exposing an executor implementation."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Any

from tianshu.executor.adapters.protocol import (
    DelegatingExecutorAdapter,
    ExecutionMode,
    ExecutorAdapter,
    PreparedExecution,
)
from tianshu.executor.capabilities import HostCapabilityProbeV1, resolve_governance_contract
from tianshu.executor.execution_gateway import ExecutionContext, bind_execution_context
from tianshu.executor.workspace_context import (
    get_bound_workspace,
    require_bound_workspace,
    requires_workspace_binding,
)
from tianshu.models.governance_contract import (
    EffectiveGovernanceContractV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind

if TYPE_CHECKING:
    from tianshu.executor.generation_controller import GenerationSelection


_BINDABLE_GENERATION_STATES = frozenset({"active", "draining"})
_MATERIALIZED_GENERATION_STATES = frozenset({"staged", "warming", "ready", "active", "draining"})
_KNOWN_GENERATION_STATES = _MATERIALIZED_GENERATION_STATES | {"failed", "disposed"}


@dataclass(frozen=True)
class PreparedExecutor:
    adapter: ExecutorAdapter
    effective: EffectiveGovernanceContractV1
    prepared: PreparedExecution
    generation_ids: tuple[str, ...] = ()
    generation_bundle: object | None = None

    def bind_run(self, run_id: str, *, instruction: str) -> PreparedExecutor:
        return PreparedExecutor(
            adapter=self.adapter,
            effective=self.effective,
            prepared=self.adapter.prepare(
                self.effective,
                run_id=run_id,
                instruction=instruction,
                execution_mode=self.prepared.execution_mode,
            ),
            generation_ids=self.generation_ids,
            generation_bundle=self.generation_bundle,
        )

    def execution_context(self, edict: Any) -> ExecutionContext | None:
        bound = get_bound_workspace()
        if requires_workspace_binding(self.effective) or bound is not None:
            bound = require_bound_workspace(
                run_id=self.prepared.run_id,
                effective_contract_hash=self.effective.content_hash,
            )
        submitter = getattr(edict, "submitter", None)
        if not submitter:
            return None
        return ExecutionContext(
            correlation_id=self.prepared.run_id,
            actor=Principal(
                id=submitter,
                kind=PrincipalKind.SERVICE,
                display_name=submitter,
            ),
            effective_contract=self.effective,
            workspace_lease_id=bound.lease.id if bound is not None else None,
        )

    async def execute(self, edict: Any, **kwargs: Any) -> Any:
        context = self.execution_context(edict)
        if context is None:
            return await self.adapter.execute(self.prepared, edict, **kwargs)
        with bind_execution_context(context):
            return await self.adapter.execute(self.prepared, edict, **kwargs)

    async def cancel(self) -> bool:
        return await self.adapter.cancel(self.prepared.run_id)


class UnsupportedExecutorMode(ValueError):
    def __init__(self, adapter_id: str, execution_mode: ExecutionMode) -> None:
        self.adapter_id = adapter_id
        self.execution_mode = execution_mode
        super().__init__(
            f"executor adapter '{adapter_id}' does not support execution mode '{execution_mode}'"
        )


class ExecutorGenerationError(RuntimeError):
    """Base error for generation-pinned executor selection."""


class ExecutorGenerationConflict(ExecutorGenerationError):
    """An attempt tried to change an already reserved generation selection."""


class ExecutorGenerationUnavailable(ExecutorGenerationError):
    """A pinned generation cannot be used without falling back to live state."""


@dataclass(frozen=True, slots=True)
class MaterializedExecutorGeneration:
    """Process-local bundle retained for one durable runtime generation."""

    generation_id: str
    scope: str
    release_digest: str
    state: str
    adapter: ExecutorAdapter
    bundle: object
    executor_manifest_digests: tuple[tuple[str, str], ...]


def _executor_scope(adapter_id: str) -> str:
    return f"executor:{adapter_id}"


def _non_blank(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-blank")
    return value


def _canonical_manifest_digests(
    values: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    if any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for name, digest in values.items()
    ):
        raise ValueError("executor manifest digests are invalid")
    return tuple(sorted(values.items()))


def _same_generation_identity(
    left: MaterializedExecutorGeneration,
    right: MaterializedExecutorGeneration,
) -> bool:
    return (
        left.generation_id == right.generation_id
        and left.scope == right.scope
        and left.release_digest == right.release_digest
        and left.adapter.adapter_id == right.adapter.adapter_id
        and left.adapter.manifest.content_hash == right.adapter.manifest.content_hash
        and left.executor_manifest_digests == right.executor_manifest_digests
    )


class ExecutorAdapterRegistry:
    def __init__(self, adapters: Iterable[ExecutorAdapter] = ()) -> None:
        self._lock = RLock()
        self._adapters: dict[str, ExecutorAdapter] = {}
        self._generation_bundles: dict[str, MaterializedExecutorGeneration] = {}
        self._attempt_leases: dict[str, tuple[tuple[str, str], ...]] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ExecutorAdapter) -> None:
        with self._lock:
            if adapter.adapter_id != adapter.manifest.adapter_id:
                raise ValueError("adapter id must match its capability manifest")
            if adapter.supported_execution_modes != adapter.manifest.execution_modes:
                raise ValueError("adapter execution modes must match its capability manifest")
            if adapter.adapter_id in self._adapters:
                raise ValueError(f"executor adapter already registered: {adapter.adapter_id}")
            self._adapters[adapter.adapter_id] = adapter

    def replace(self, adapter: ExecutorAdapter) -> None:
        """Replace an adapter for composition/tests; P3 generation APIs will own activation."""

        with self._lock:
            if adapter.adapter_id != adapter.manifest.adapter_id:
                raise ValueError("adapter id must match its capability manifest")
            if adapter.supported_execution_modes != adapter.manifest.execution_modes:
                raise ValueError("adapter execution modes must match its capability manifest")
            self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> ExecutorAdapter:
        with self._lock:
            try:
                return self._adapters[adapter_id]
            except KeyError as exc:
                raise KeyError(f"unknown executor adapter: {adapter_id}") from exc

    def manifest_digests(self) -> dict[str, str]:
        """Return a sorted copy of the active adapter manifest content hashes."""

        with self._lock:
            return self._static_manifest_digests()

    def _static_manifest_digests(self) -> dict[str, str]:
        return {
            adapter_id: self._adapters[adapter_id].manifest.content_hash
            for adapter_id in sorted(self._adapters)
        }

    @contextmanager
    def generation_guard(self) -> Iterator[None]:
        """Serialize repository selection with the process-local bundle snapshot."""

        with self._lock:
            yield

    def install_generation(
        self,
        *,
        generation_id: str,
        scope: str,
        release_digest: str,
        state: str,
        adapter: ExecutorAdapter,
        bundle: object,
        executor_manifest_digests: Mapping[str, str] | None = None,
    ) -> MaterializedExecutorGeneration:
        """Retain one materialized bundle without changing durable generation state."""

        _non_blank(generation_id, field="generation_id")
        _non_blank(scope, field="scope")
        _non_blank(release_digest, field="release_digest")
        if state not in _MATERIALIZED_GENERATION_STATES:
            raise ExecutorGenerationUnavailable(
                f"generation {generation_id!r} is not materializable in state {state!r}"
            )
        if scope != _executor_scope(adapter.adapter_id):
            raise ExecutorGenerationConflict("generation scope does not match its adapter")
        if adapter.adapter_id != adapter.manifest.adapter_id:
            raise ValueError("adapter id must match its capability manifest")
        if adapter.supported_execution_modes != adapter.manifest.execution_modes:
            raise ValueError("adapter execution modes must match its capability manifest")
        manifest_digests = _canonical_manifest_digests(
            executor_manifest_digests or {adapter.adapter_id: adapter.manifest.content_hash}
        )
        if dict(manifest_digests).get(adapter.adapter_id) != adapter.manifest.content_hash:
            raise ValueError("generation manifest digest must match its executor adapter")
        record = MaterializedExecutorGeneration(
            generation_id=generation_id,
            scope=scope,
            release_digest=release_digest,
            state=state,
            adapter=adapter,
            bundle=bundle,
            executor_manifest_digests=manifest_digests,
        )
        with self._lock:
            existing = self._generation_bundles.get(generation_id)
            if existing is not None:
                same_identity = _same_generation_identity(existing, record)
                same_identity = same_identity and existing.state == record.state
                if not same_identity:
                    raise ExecutorGenerationConflict(
                        f"generation bundle already installed: {generation_id}"
                    )
                return existing
            self._generation_bundles[generation_id] = record
            return record

    def reconcile_generation(
        self,
        *,
        generation_id: str,
        scope: str,
        release_digest: str,
        state: str,
        adapter: ExecutorAdapter,
        bundle: object,
        executor_manifest_digests: Mapping[str, str] | None = None,
    ) -> MaterializedExecutorGeneration:
        """Restore one durable materialized identity when no attempt can observe it."""

        _non_blank(generation_id, field="generation_id")
        _non_blank(scope, field="scope")
        _non_blank(release_digest, field="release_digest")
        if state not in _MATERIALIZED_GENERATION_STATES:
            raise ExecutorGenerationUnavailable(
                f"generation {generation_id!r} is not materializable in state {state!r}"
            )
        if scope != _executor_scope(adapter.adapter_id):
            raise ExecutorGenerationConflict("generation scope does not match its adapter")
        if adapter.adapter_id != adapter.manifest.adapter_id:
            raise ValueError("adapter id must match its capability manifest")
        if adapter.supported_execution_modes != adapter.manifest.execution_modes:
            raise ValueError("adapter execution modes must match its capability manifest")
        manifest_digests = _canonical_manifest_digests(
            executor_manifest_digests or {adapter.adapter_id: adapter.manifest.content_hash}
        )
        if dict(manifest_digests).get(adapter.adapter_id) != adapter.manifest.content_hash:
            raise ValueError("generation manifest digest must match its executor adapter")
        record = MaterializedExecutorGeneration(
            generation_id=generation_id,
            scope=scope,
            release_digest=release_digest,
            state=state,
            adapter=adapter,
            bundle=bundle,
            executor_manifest_digests=manifest_digests,
        )
        with self._lock:
            existing = self._generation_bundles.get(generation_id)
            if existing is not None and not _same_generation_identity(existing, record):
                raise ExecutorGenerationConflict(
                    f"generation bundle identity conflicts with durable truth: {generation_id}"
                )
            if existing is not None and self._generation_is_leased_unlocked(generation_id):
                raise ExecutorGenerationConflict(
                    f"cannot reconcile a leased generation bundle: {generation_id}"
                )
            self._generation_bundles[generation_id] = record
            return record

    def reconcile_generation_state(
        self,
        generation_id: str,
        state: str,
        *,
        expected_scope: str,
        expected_release_digest: str,
        expected_manifest_digests: Mapping[str, str],
    ) -> MaterializedExecutorGeneration:
        """Repair only state after strict durable identity and lease checks."""

        _non_blank(generation_id, field="generation_id")
        _non_blank(expected_scope, field="expected_scope")
        _non_blank(expected_release_digest, field="expected_release_digest")
        if state not in _KNOWN_GENERATION_STATES:
            raise ValueError(f"unknown generation state: {state!r}")
        manifest_digests = _canonical_manifest_digests(expected_manifest_digests)
        with self._lock:
            try:
                current = self._generation_bundles[generation_id]
            except KeyError as exc:
                raise ExecutorGenerationUnavailable(
                    f"generation bundle is not materialized: {generation_id}"
                ) from exc
            if (
                current.scope != expected_scope
                or current.release_digest != expected_release_digest
                or current.executor_manifest_digests != manifest_digests
            ):
                raise ExecutorGenerationConflict(
                    f"generation bundle identity conflicts with durable truth: {generation_id}"
                )
            if current.state != state and self._generation_is_leased_unlocked(generation_id):
                raise ExecutorGenerationConflict(
                    f"cannot reconcile a leased generation bundle: {generation_id}"
                )
            updated = MaterializedExecutorGeneration(
                generation_id=current.generation_id,
                scope=current.scope,
                release_digest=current.release_digest,
                state=state,
                adapter=current.adapter,
                bundle=current.bundle,
                executor_manifest_digests=current.executor_manifest_digests,
            )
            self._generation_bundles[generation_id] = updated
            return updated

    def update_generation_state(
        self,
        generation_id: str,
        state: str,
    ) -> MaterializedExecutorGeneration:
        """Mirror a committed repository transition into the retained bundle view."""

        if state not in _KNOWN_GENERATION_STATES:
            raise ValueError(f"unknown generation state: {state!r}")
        with self._lock:
            try:
                current = self._generation_bundles[generation_id]
            except KeyError as exc:
                raise ExecutorGenerationUnavailable(
                    f"generation bundle is not materialized: {generation_id}"
                ) from exc
            updated = MaterializedExecutorGeneration(
                generation_id=current.generation_id,
                scope=current.scope,
                release_digest=current.release_digest,
                state=state,
                adapter=current.adapter,
                bundle=current.bundle,
                executor_manifest_digests=current.executor_manifest_digests,
            )
            self._generation_bundles[generation_id] = updated
            return updated

    def remove_generation(self, generation_id: str) -> object | None:
        """Drop an unleased disposed/failed bundle from process memory."""

        with self._lock:
            record = self._generation_bundles.get(generation_id)
            if record is None:
                return None
            if record.state not in {"disposed", "failed"}:
                raise ExecutorGenerationConflict(
                    "only disposed or failed generation bundles can be removed"
                )
            if any(
                leased_generation_id == generation_id
                for lease in self._attempt_leases.values()
                for _, leased_generation_id in lease
            ):
                raise ExecutorGenerationConflict("cannot remove a leased generation bundle")
            self._generation_bundles.pop(generation_id)
            return record.bundle

    def generation_record(
        self,
        generation_id: str,
    ) -> MaterializedExecutorGeneration | None:
        """Return the immutable retained record for controller/reconciler coordination."""

        with self._lock:
            return self._generation_bundles.get(generation_id)

    def generation_records(self) -> tuple[MaterializedExecutorGeneration, ...]:
        """Return a stable snapshot for durable reconciliation/readiness checks."""

        with self._lock:
            return tuple(
                self._generation_bundles[generation_id]
                for generation_id in sorted(self._generation_bundles)
            )

    def _generation_is_leased_unlocked(self, generation_id: str) -> bool:
        return any(
            leased_generation_id == generation_id
            for lease in self._attempt_leases.values()
            for _, leased_generation_id in lease
        )

    def reserve_binding(
        self,
        attempt_id: str,
        *,
        pinned_ids: tuple[str, ...],
        required_scopes: tuple[str, ...],
        allow_ready: bool = False,
    ) -> GenerationSelection:
        """Reserve an exact, canonical generation set for one durable attempt."""

        from tianshu.executor.generation_controller import GenerationSelection

        _non_blank(attempt_id, field="attempt_id")
        if not isinstance(pinned_ids, tuple) or any(
            not isinstance(generation_id, str) or not generation_id.strip()
            for generation_id in pinned_ids
        ):
            raise TypeError("pinned_ids must be a tuple of non-blank strings")
        if len(set(pinned_ids)) != len(pinned_ids):
            raise ExecutorGenerationConflict("generation selection contains duplicate ids")
        if not isinstance(required_scopes, tuple) or any(
            not isinstance(scope, str) or not scope.strip() for scope in required_scopes
        ):
            raise TypeError("required_scopes must be a tuple of non-blank strings")
        if len(set(required_scopes)) != len(required_scopes):
            raise ExecutorGenerationConflict("generation selection contains duplicate scopes")
        canonical_scopes = tuple(sorted(required_scopes))
        allowed_states = (
            _BINDABLE_GENERATION_STATES | {"ready"} if allow_ready else _BINDABLE_GENERATION_STATES
        )
        with self._lock:
            records: list[MaterializedExecutorGeneration] = []
            for generation_id in pinned_ids:
                record = self._generation_bundles.get(generation_id)
                if record is None:
                    raise ExecutorGenerationUnavailable(
                        f"generation bundle is not materialized: {generation_id}"
                    )
                if record.state not in allowed_states:
                    raise ExecutorGenerationUnavailable(
                        f"generation {generation_id!r} is unavailable in state {record.state!r}"
                    )
                records.append(record)
            records.sort(key=lambda item: (item.scope, item.generation_id))
            selected_scopes = tuple(record.scope for record in records)
            if len(set(selected_scopes)) != len(selected_scopes):
                raise ExecutorGenerationConflict("generation selection contains duplicate scopes")
            if records and selected_scopes != canonical_scopes:
                raise ExecutorGenerationConflict(
                    "generation selection does not match the required scopes"
                )
            canonical_lease = tuple((record.scope, record.generation_id) for record in records)
            existing_lease = self._attempt_leases.get(attempt_id)
            if existing_lease is not None and existing_lease != canonical_lease:
                raise ExecutorGenerationConflict("attempt generation selection is already reserved")
            manifest_digests = self._static_manifest_digests()
            for record in records:
                for adapter_id, digest in record.executor_manifest_digests:
                    manifest_digests[adapter_id] = digest
            selection = GenerationSelection(
                generation_ids=tuple(record.generation_id for record in records),
                by_scope={record.scope: record.generation_id for record in records},
                executor_manifest_digests=manifest_digests,
                bundles={record.scope: record.bundle for record in records},
            )
            self._attempt_leases.setdefault(attempt_id, canonical_lease)
            return selection

    def release(self, attempt_id: str) -> bool:
        """Release one exact-attempt lease; repeated or late releases are harmless."""

        _non_blank(attempt_id, field="attempt_id")
        with self._lock:
            return self._attempt_leases.pop(attempt_id, None) is not None

    def attempt_leases(self) -> dict[str, tuple[tuple[str, str], ...]]:
        """Return a detached snapshot for reconciliation and status projection."""

        with self._lock:
            return dict(self._attempt_leases)

    def active_attempt_count(self, generation_id: str) -> int:
        with self._lock:
            return sum(
                leased_generation_id == generation_id
                for lease in self._attempt_leases.values()
                for _, leased_generation_id in lease
            )

    def prepare(
        self,
        requested: RequestedGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
        attempt_id: str | None = None,
    ) -> PreparedExecutor:
        adapter, generation_ids, generation_bundle = self._adapter_for_attempt(
            requested.executor.adapter_id,
            attempt_id=attempt_id,
        )
        if execution_mode not in adapter.supported_execution_modes:
            raise UnsupportedExecutorMode(adapter.adapter_id, execution_mode)
        probe = adapter.probe()
        effective = resolve_governance_contract(requested, adapter.manifest, probe)
        return self._bind_verified_effective(
            adapter,
            effective,
            probe=probe,
            run_id=run_id,
            instruction=instruction,
            execution_mode=execution_mode,
            generation_ids=generation_ids,
            generation_bundle=generation_bundle,
        )

    def bind_effective(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
        attempt_id: str | None = None,
    ) -> PreparedExecutor:
        adapter, generation_ids, generation_bundle = self._adapter_for_attempt(
            effective.executor.adapter_id,
            attempt_id=attempt_id,
        )
        if execution_mode not in adapter.supported_execution_modes:
            raise UnsupportedExecutorMode(adapter.adapter_id, execution_mode)
        return self._bind_verified_effective(
            adapter,
            effective,
            probe=adapter.probe(),
            run_id=run_id,
            instruction=instruction,
            execution_mode=execution_mode,
            generation_ids=generation_ids,
            generation_bundle=generation_bundle,
        )

    def _adapter_for_attempt(
        self,
        adapter_id: str,
        *,
        attempt_id: str | None,
    ) -> tuple[ExecutorAdapter, tuple[str, ...], object | None]:
        with self._lock:
            if attempt_id is None:
                return self.get(adapter_id), (), None
            lease = self._attempt_leases.get(attempt_id)
            if lease is None:
                raise ExecutorGenerationUnavailable(
                    f"attempt generation selection is not reserved: {attempt_id}"
                )
            if not lease:
                return self.get(adapter_id), (), None
            expected_scope = _executor_scope(adapter_id)
            matching = [generation_id for scope, generation_id in lease if scope == expected_scope]
            executor_scopes = [scope for scope, _ in lease if scope.startswith("executor:")]
            if not matching:
                if executor_scopes:
                    raise ExecutorGenerationConflict(
                        "attempt executor does not match its generation selection"
                    )
                return self.get(adapter_id), tuple(item[1] for item in lease), None
            generation_id = matching[0]
            record = self._generation_bundles.get(generation_id)
            if record is None or record.state not in (_BINDABLE_GENERATION_STATES | {"ready"}):
                raise ExecutorGenerationUnavailable(
                    f"attempt generation bundle is unavailable: {generation_id}"
                )
            return (
                record.adapter,
                tuple(item[1] for item in lease),
                record.bundle,
            )

    def _bind_verified_effective(
        self,
        adapter: ExecutorAdapter,
        effective: EffectiveGovernanceContractV1,
        *,
        probe: HostCapabilityProbeV1,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
        generation_ids: tuple[str, ...] = (),
        generation_bundle: object | None = None,
    ) -> PreparedExecutor:
        manifest = adapter.manifest
        if (
            effective.executor_manifest_id != manifest.manifest_id
            or effective.executor_manifest_version != manifest.manifest_version
            or effective.executor_manifest_hash != manifest.content_hash
        ):
            raise ValueError("persisted effective contract has executor manifest drift")
        if effective.runtime_probe_id != probe.semantic_id:
            raise ValueError("persisted effective contract has host capability probe drift")
        prepared = adapter.prepare(
            effective,
            run_id=run_id,
            instruction=instruction,
            execution_mode=execution_mode,
        )
        return PreparedExecutor(
            adapter=adapter,
            effective=effective,
            prepared=prepared,
            generation_ids=generation_ids,
            generation_bundle=generation_bundle,
        )


__all__ = [
    "DelegatingExecutorAdapter",
    "ExecutionMode",
    "ExecutorAdapter",
    "ExecutorAdapterRegistry",
    "ExecutorGenerationConflict",
    "ExecutorGenerationError",
    "ExecutorGenerationUnavailable",
    "MaterializedExecutorGeneration",
    "PreparedExecution",
    "PreparedExecutor",
    "UnsupportedExecutorMode",
]
