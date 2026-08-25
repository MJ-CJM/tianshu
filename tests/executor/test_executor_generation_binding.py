"""Executor consumption of the exact generation selected by Router."""

from __future__ import annotations

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.evolution.runtime_context import RunBindingContextV1, bind_run_binding
from tianshu.executor.adapters import DelegatingExecutorAdapter
from tianshu.executor.capabilities import pi_manifest
from tianshu.executor.executor import Executor
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial

_GENERATION_ID = "rg-" + "1" * 32


def _executor(storage, config_manager) -> Executor:
    return Executor(
        event_bus=EventBus(),
        storage=storage,
        config_manager=config_manager,
        hook_registry=HookRegistry(),
    )


def _edict_and_memorial() -> tuple[Edict, Memorial]:
    edict = Edict(
        id="edict-generation",
        goal="use pinned pi",
        runtime={"executor": "keqing:pi"},
    )
    return edict, Memorial(id="memorial-generation", edict_id=edict.id)


def test_nonempty_run_binding_selects_reserved_generation(storage, config_manager) -> None:
    executor = _executor(storage, config_manager)
    edict, memorial = _edict_and_memorial()
    generation_adapter = DelegatingExecutorAdapter(
        adapter_id="keqing:pi",
        manifest=pi_manifest(),
        delegate=object(),
    )
    executor.adapter_registry.install_generation(
        generation_id=_GENERATION_ID,
        scope="executor:keqing:pi",
        release_digest="a" * 64,
        state="active",
        adapter=generation_adapter,
        bundle=object(),
    )
    executor.adapter_registry.reserve_binding(
        "attempt-generation",
        pinned_ids=(_GENERATION_ID,),
        required_scopes=("executor:keqing:pi",),
    )
    binding = RunBindingContextV1(
        memorial_id=memorial.id,
        attempt_id="attempt-generation",
        generation_ids=(_GENERATION_ID,),
    )

    with bind_run_binding(binding):
        prepared = executor._resolve_governed_executor(  # noqa: SLF001
            edict,
            memorial,
            execution_mode="single",
        )

    assert prepared.adapter is generation_adapter
    assert prepared.generation_ids == (_GENERATION_ID,)
    assert prepared.generation_bundle is not None


def test_empty_binding_preserves_static_registry_without_a_lease(storage, config_manager) -> None:
    executor = _executor(storage, config_manager)
    edict, memorial = _edict_and_memorial()
    binding = RunBindingContextV1(
        memorial_id=memorial.id,
        attempt_id="attempt-shadow",
        generation_ids=(),
    )

    with bind_run_binding(binding):
        prepared = executor._resolve_governed_executor(  # noqa: SLF001
            edict,
            memorial,
            execution_mode="single",
        )

    assert prepared.adapter is executor.adapter_registry.get("keqing:pi")
    assert prepared.generation_ids == ()
    assert prepared.generation_bundle is None


def test_generation_context_for_another_memorial_fails_closed(storage, config_manager) -> None:
    executor = _executor(storage, config_manager)
    edict, memorial = _edict_and_memorial()
    binding = RunBindingContextV1(
        memorial_id="different-memorial",
        attempt_id="attempt-generation",
        generation_ids=(_GENERATION_ID,),
    )

    with (
        bind_run_binding(binding),
        pytest.raises(
            RuntimeError,
            match="does not match memorial",
        ),
    ):
        executor._resolve_governed_executor(  # noqa: SLF001
            edict,
            memorial,
            execution_mode="single",
        )
