"""Run-scoped and platform-wide cost accounting contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tianshu.bootstrap.wiring_llm import wire_cost_manager
from tianshu.bus.event_bus import EventBus
from tianshu.cost.manager import CostManager
from tianshu.cost.models import CostRecord
from tianshu.llm import LLMUsageContext, set_usage_observer
from tianshu.models import Edict, UsageSummary
from tianshu.models.events import EventEnvelope


def _usage(tokens: int, cost: float, cache_read_tokens: int = 0) -> UsageSummary:
    return UsageSummary(
        prompt_tokens=tokens,
        completion_tokens=0,
        total_tokens=tokens,
        cache_read_tokens=cache_read_tokens,
        cost_cny=cost,
        actual_model="actual-model",
    )


@pytest.mark.asyncio
async def test_concurrent_memorials_do_not_merge_cost(storage) -> None:
    manager = CostManager(storage)
    edict = type("EdictRef", (), {"id": "edict-1"})()
    first = type("MemorialRef", (), {"id": "memorial-1"})()
    second = type("MemorialRef", (), {"id": "memorial-2"})()

    await manager.on_llm_output(
        edict=edict,
        memorial=first,
        usage=_usage(10, 0.1),
        provider_name="provider-a",
        config_state=type("Config", (), {"model": "configured-model"})(),
    )
    await manager.on_llm_output(
        edict=edict,
        memorial=second,
        usage=_usage(20, 0.2),
        provider_name="provider-b",
        config_state=type("Config", (), {"model": "configured-model"})(),
    )

    await manager.handle_execution_completed(
        EventEnvelope(
            event_type="execution.completed",
            edict_id="edict-1",
            memorial_id="memorial-1",
            producer="test",
        )
    )
    await manager.handle_execution_completed(
        EventEnvelope(
            event_type="execution.completed",
            edict_id="edict-1",
            memorial_id="memorial-2",
            producer="test",
        )
    )

    records, total = storage.list_cost_records(edict_id="edict-1")
    assert total == 2
    by_memorial = {record["memorial_id"]: record for record in records}
    assert by_memorial["memorial-1"]["total_tokens"] == 10
    assert by_memorial["memorial-1"]["provider_name"] == "provider-a"
    assert by_memorial["memorial-2"]["total_tokens"] == 20
    assert by_memorial["memorial-2"]["provider_name"] == "provider-b"


@pytest.mark.asyncio
async def test_cancelled_execution_persists_cost_once_and_releases_tracker(storage) -> None:
    manager = CostManager(storage)
    edict = type("EdictRef", (), {"id": "edict-cancelled"})()
    memorial = type("MemorialRef", (), {"id": "memorial-cancelled"})()
    await manager.on_llm_output(
        edict=edict,
        memorial=memorial,
        usage=_usage(11, 0.11),
        provider_name="provider-c",
        config_state=type("Config", (), {"model": "configured-model"})(),
    )
    event = EventEnvelope(
        event_type="execution.cancelled",
        edict_id=edict.id,
        memorial_id=memorial.id,
        producer="test",
    )

    await manager.handle_execution_cancelled(event)
    await manager.handle_execution_completed(
        event.model_copy(update={"event_type": "execution.completed"})
    )

    records, total = storage.list_cost_records(edict_id=edict.id)
    assert total == 1
    assert records[0]["memorial_id"] == memorial.id
    assert records[0]["total_tokens"] == 11
    assert manager._trackers == {}  # noqa: SLF001 - tracker lifecycle contract


def test_cost_wiring_subscribes_to_execution_cancelled(storage) -> None:
    event_bus = EventBus()
    app = SimpleNamespace(state=SimpleNamespace(storage=storage, event_bus=event_bus))
    settings = SimpleNamespace(daily_budget_guardrail_cny=0)

    try:
        wire_cost_manager(app, settings)
        consumers = {
            entry.consumer_name
            for entry in event_bus._handlers["execution.cancelled"]  # noqa: SLF001
        }
        assert consumers == {"cost.execution_cancelled.v1"}
    finally:
        set_usage_observer(None)


def test_unattributed_platform_llm_call_is_not_silently_dropped(storage) -> None:
    manager = CostManager(storage)

    manager.observe_llm_usage(
        _usage(7, 0.07, cache_read_tokens=3),
        LLMUsageContext(operation="memory_compaction"),
        "provider",
        "configured-model",
    )

    records, total = storage.list_cost_records(edict_id="__platform__")
    assert total == 1
    assert records[0]["total_tokens"] == 7
    assert records[0]["cache_read_tokens"] == 3
    assert records[0]["model"] == "actual-model"


def test_live_usage_snapshot_is_scoped_to_one_durable_run(storage) -> None:
    manager = CostManager(storage)
    manager.observe_llm_usage(
        _usage(13, 0.13),
        LLMUsageContext(edict_id="edict-1", memorial_id="memorial-1"),
        "provider",
        "model",
    )

    usage = manager.get_live_usage("edict-1", "memorial-1")
    assert usage is not None
    assert usage.total_tokens == 13
    assert usage.cost_cny == pytest.approx(0.13)
    assert manager.get_live_usage("edict-1", "memorial-2") is None


@pytest.mark.asyncio
async def test_final_record_labels_mixed_models_and_providers_truthfully(storage) -> None:
    manager = CostManager(storage)
    context = LLMUsageContext(edict_id="edict-mixed", memorial_id="memorial-mixed")
    manager.observe_llm_usage(_usage(5, 0.05), context, "provider-a", "model-a")
    second = _usage(7, 0.07).model_copy(update={"actual_model": "actual-model-b"})
    manager.observe_llm_usage(second, context, "provider-b", "model-b")

    await manager.handle_execution_completed(
        EventEnvelope(
            event_type="execution.completed",
            edict_id="edict-mixed",
            memorial_id="memorial-mixed",
            producer="test",
        )
    )

    records, total = storage.list_cost_records(edict_id="edict-mixed")
    assert total == 1
    assert records[0]["provider_name"] == "multiple"
    assert records[0]["model"] == "multiple"


@pytest.mark.asyncio
async def test_record_accumulates_and_enforces_matching_submitter_budget(storage) -> None:
    manager = CostManager(storage)
    owner = Edict(id="edict-owner", goal="owned task", submitter="user:owner")
    storage.save_edict(owner)
    manager.set_budget("submitter:user:owner", 0.5)
    manager.set_budget("submitter:user:other", 0.5)

    manager.record(CostRecord(edict_id=owner.id, cost_cny=0.5))

    owner_budget = manager.get_budget("submitter:user:owner")
    other_budget = manager.get_budget("submitter:user:other")
    assert owner_budget is not None
    assert owner_budget.spent_cny == pytest.approx(0.5)
    assert owner_budget.exceeded is True
    assert other_budget is not None
    assert other_budget.spent_cny == 0.0

    result = await manager.on_before_iteration(edict=owner)
    assert result is not None
    assert result.block is True
    assert result.reason.startswith("Submitter budget exceeded:")
