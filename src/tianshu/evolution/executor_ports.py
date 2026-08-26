"""Structural ports used by evolution without importing executor implementations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from tianshu.models.executor_generation import GenerationRecoveryReport
from tianshu.models.runtime_generation import (
    GenerationPointerV1,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage.generation_repo import (
    GenerationActivationResult,
    GenerationRollbackResult,
)


class RuntimeReleaseMaterializerPort(Protocol):
    def create_release(self) -> RuntimeReleaseV1: ...

    def verify_release(self, release: RuntimeReleaseV1) -> object: ...


class GenerationControlPort(Protocol):
    def recover(
        self,
        *,
        pre_active_root_ids: frozenset[str] = frozenset(),
    ) -> GenerationRecoveryReport: ...

    def stage_exact(
        self,
        release: RuntimeReleaseV1,
        *,
        generation_id: str,
        stage_commit_hook: Callable[[sqlite3.Connection, RuntimeGenerationV1], None] | None = None,
    ) -> RuntimeGenerationV1: ...

    async def warm_or_resume(self, generation_id: str) -> RuntimeGenerationV1: ...

    def activate(self, generation_id: str) -> GenerationActivationResult: ...

    def activate_exact(
        self,
        generation_id: str,
        *,
        expected_active_generation_id: str,
        expected_active_release_digest: str,
        activation_commit_hook: Callable[
            [sqlite3.Connection, RuntimeGenerationV1, GenerationPointerV1 | None], None
        ]
        | None = None,
    ) -> GenerationActivationResult: ...

    def fail_pre_active_exact(
        self,
        scope: str,
        *,
        generation_id: str,
        expected_release_digest: str,
        failure_commit_hook: Callable[[sqlite3.Connection, RuntimeGenerationV1], None]
        | None = None,
    ) -> RuntimeGenerationV1: ...

    def rollback_exact(
        self,
        scope: str,
        *,
        expected_active_generation_id: str,
        expected_last_good_generation_id: str,
    ) -> GenerationRollbackResult: ...


class ExecutorAdapterIdentityPort(Protocol):
    @property
    def adapter_id(self) -> str: ...


class MaterializedExecutorGenerationPort(Protocol):
    @property
    def generation_id(self) -> str: ...

    @property
    def scope(self) -> str: ...

    @property
    def release_digest(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def adapter(self) -> ExecutorAdapterIdentityPort: ...

    @property
    def executor_manifest_digests(self) -> tuple[tuple[str, str], ...]: ...


class GenerationRegistryPort(Protocol):
    def generation_guard(self) -> AbstractContextManager[None]: ...

    def active_attempt_count(self, generation_id: str) -> int: ...

    def generation_record(
        self,
        generation_id: str,
    ) -> MaterializedExecutorGenerationPort | None: ...

    def generation_records(self) -> tuple[MaterializedExecutorGenerationPort, ...]: ...

    def update_generation_state(self, generation_id: str, state: str) -> object: ...

    def reconcile_generation_state(
        self,
        generation_id: str,
        state: str,
        *,
        expected_scope: str,
        expected_release_digest: str,
        expected_manifest_digests: dict[str, str],
    ) -> object: ...

    def remove_generation(self, generation_id: str) -> object | None: ...


__all__ = [
    "GenerationControlPort",
    "GenerationRegistryPort",
    "MaterializedExecutorGenerationPort",
    "RuntimeReleaseMaterializerPort",
]
