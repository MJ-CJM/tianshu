"""Startup bootstrap for the first trusted executor runtime generation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from tianshu.evolution.executor_ports import (
    GenerationControlPort,
    RuntimeReleaseMaterializerPort,
)
from tianshu.models.canonical import canonical_sha256
from tianshu.models.executor_generation import PI_GENERATION_SCOPE, GenerationRecoveryReport
from tianshu.models.runtime_generation import (
    GenerationPointerV1,
    RuntimeGenerationState,
    RuntimeReleaseV1,
)
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

type UnitOfWorkFactory = Callable[[], SqliteUnitOfWork]


@dataclass(frozen=True, slots=True)
class ExecutorGenerationBootstrapReport:
    """Stable startup result without exposing managed package paths."""

    enabled: bool
    bootstrapped: bool
    active_generation_id: str | None
    release_digest: str | None
    recovery: GenerationRecoveryReport


class ExecutorGenerationBootstrap:
    """Recover generations and establish the first active Pi baseline once."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        controller: GenerationControlPort,
        materializer: RuntimeReleaseMaterializerPort,
        enabled: bool,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._controller = controller
        self._materializer = materializer
        self._enabled = enabled
        self._repository = GenerationRepository()

    async def initialize(self) -> ExecutorGenerationBootstrapReport:
        """Recover first; when enabled, create one trusted baseline if absent."""

        pointer = self._pointer()
        if not self._enabled:
            recovery = self._controller.recover()
            return ExecutorGenerationBootstrapReport(
                enabled=False,
                bootstrapped=False,
                active_generation_id=(
                    pointer.active_generation_id if pointer is not None else None
                ),
                release_digest=self._active_release_digest(pointer),
                recovery=recovery,
            )
        if pointer is not None:
            recovery = self._controller.recover()
            return ExecutorGenerationBootstrapReport(
                enabled=True,
                bootstrapped=False,
                active_generation_id=pointer.active_generation_id,
                release_digest=self._active_release_digest(pointer),
                recovery=recovery,
            )

        release = await asyncio.to_thread(self._materializer.create_release)
        generation_id = self._select_generation_id(release)
        recovery = self._controller.recover(pre_active_root_ids=frozenset({generation_id}))
        self._controller.stage_exact(release, generation_id=generation_id)
        ready = await self._controller.warm_or_resume(generation_id)
        if ready.state is not RuntimeGenerationState.READY:
            raise RuntimeError("executor baseline did not reach ready state")
        activated = self._controller.activate(generation_id).activated
        return ExecutorGenerationBootstrapReport(
            enabled=True,
            bootstrapped=True,
            active_generation_id=activated.generation_id,
            release_digest=activated.release_digest,
            recovery=recovery,
        )

    def _pointer(self) -> GenerationPointerV1 | None:
        with self._unit_of_work_factory() as unit_of_work:
            pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=PI_GENERATION_SCOPE,
            )
            unit_of_work.commit()
            return pointer

    def _active_release_digest(self, pointer: GenerationPointerV1 | None) -> str | None:
        if pointer is None:
            return None
        with self._unit_of_work_factory() as unit_of_work:
            generation = self._repository.get_generation(
                unit_of_work.connection,
                scope=PI_GENERATION_SCOPE,
                generation_id=pointer.active_generation_id,
            )
            unit_of_work.commit()
        if generation is None or generation.state is not RuntimeGenerationState.ACTIVE:
            raise RuntimeError("executor generation pointer is invalid")
        return generation.release_digest

    def _select_generation_id(self, release: RuntimeReleaseV1) -> str:
        attempt = 1
        while True:
            identity = canonical_sha256(
                {
                    "attempt": attempt,
                    "purpose": "executor-baseline",
                    "release_digest": release.release_digest,
                    "schema_version": 1,
                }
            )
            generation_id = f"rg-{identity[:32]}"
            with self._unit_of_work_factory() as unit_of_work:
                existing = self._repository.get_generation(
                    unit_of_work.connection,
                    scope=release.scope,
                    generation_id=generation_id,
                )
                unit_of_work.commit()
            if existing is None:
                return generation_id
            if existing.release_digest == release.release_digest and existing.state in {
                RuntimeGenerationState.STAGED,
                RuntimeGenerationState.WARMING,
                RuntimeGenerationState.READY,
            }:
                return generation_id
            attempt += 1


__all__ = [
    "ExecutorGenerationBootstrap",
    "ExecutorGenerationBootstrapReport",
]
