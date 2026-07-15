"""Restart, race, and replay coverage for the durable tool-decision core."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

from tianshu.application.outbox import OutboxDispatcher
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.governance.decision_service import DecisionConflict, DecisionService
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.decision import DecisionKind, ResolveDecisionCommand
from tianshu.models.principal import AuthContext, Principal
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.tools.policy_store import InMemorySessionRuleStore

_NOW = datetime(2026, 7, 15, 13, tzinfo=UTC)


def _auth(identity: str, correlation_id: str) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=identity,
            kind="human",
            display_name=identity.rsplit(":", 1)[-1],
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id=correlation_id,
    )


def _create_database(path: Path) -> tuple[Edict, Memorial]:
    storage = Storage(str(path))
    storage.init_db()
    edict = Edict(id="edict-tool-restart", goal="durable tool approval")
    memorial = Memorial(
        id="memorial-tool-restart",
        edict_id=edict.id,
        status=TaskStatus.RUNNING,
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    storage.close()
    return edict, memorial


def _manager(
    storage: Storage,
    *,
    event_bus: EventBus | None = None,
) -> tuple[ApprovalManager, DecisionService]:
    service = DecisionService(storage, clock=lambda: _NOW)
    manager = ApprovalManager(
        event_bus=event_bus or EventBus(),
        storage=storage,
        decision_service=service,
        clock=lambda: _NOW,
    )
    return manager, service


def _suspend(
    manager: ApprovalManager,
    edict: Edict,
    memorial: Memorial,
    *,
    invocation_id: str = "tool-call-restart-1",
    iteration: int = 2,
    tool_name: str = "write_file",
):
    return manager.request_tool_decision(
        edict=edict,
        memorial=memorial,
        invocation_id=invocation_id,
        tool_name=tool_name,
        tool_args={"path": "README.md", "command": "git status"},
        tool_tier="T1_WORKSPACE",
        policy_rule_id="approval_required",
        messages=[
            {"role": "system", "content": "system"},
            {
                "role": "assistant",
                "content": "calling",
                "reasoning_content": "durable reasoning",
                "tool_calls": [
                    {
                        "id": invocation_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": {"path": "README.md", "command": "git status"},
                        },
                    }
                ],
            },
        ],
        iteration=iteration,
        usage=UsageSummary(total_tokens=9),
    )


def _resolution() -> ResolveDecisionCommand:
    return ResolveDecisionCommand(
        action="approve",
        reason="review complete",
        payload={"schema_version": 1, "grant_scope": "once", "grant_reason": None},
        expected_version=1,
    )


async def test_tool_suspension_survives_restart_and_polls_without_waiter(tmp_path: Path) -> None:
    path = tmp_path / "tool-restart.db"
    edict, memorial = _create_database(path)
    first = Storage(str(path))
    first.init_db()
    first_manager, _ = _manager(first)
    requested = _suspend(first_manager, edict, memorial)
    first.close()

    restarted = Storage(str(path))
    restarted.init_db()
    try:
        manager, service = _manager(restarted)
        assert service.list_pending(kind=DecisionKind.TOOL) == [requested]
        with restarted.unit_of_work() as unit_of_work:
            state = restarted.run_state_repo.load(unit_of_work.connection, memorial.id)
            unit_of_work.commit()
        assert state is not None and state.phase.value == "waiting_decision"
        assert state.continuation.pending_decision_id == requested.decision_request_id
        service.resolve(
            requested.decision_request_id,
            _resolution(),
            auth=_auth("user:reviewer", "restart-resolve"),
        )

        resolution = await manager.wait_for_tool_decision(
            requested.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )
        replayed = await manager.wait_for_tool_decision(
            requested.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )

        assert resolution is not None and resolution.action == "approve"
        assert replayed == resolution
        [decree] = restarted.list_decrees_by_memorial(memorial.id)
        assert decree.id == requested.decision_request_id
        assert decree.action == "approve"
        assert decree.actor == "user:reviewer"
        assert (
            restarted._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'decision.resolved'"
            ).fetchone()[0]
            == 1
        )
    finally:
        restarted.close()


async def test_projection_failure_replays_from_durable_decision_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "tool-projection-replay.db"
    edict, memorial = _create_database(path)
    storage = Storage(str(path))
    storage.init_db()
    bus = EventBus()
    manager, service = _manager(storage, event_bus=bus)
    requested = _suspend(manager, edict, memorial)
    service.resolve(
        requested.decision_request_id,
        _resolution(),
        auth=_auth("user:reviewer", "projection-resolve"),
    )
    bus.on(
        "decision.resolved",
        manager.handle_decision_resolved,
        consumer_name="approval_manager.tool_decree_projection.v1",
    )
    clock = [_NOW]
    dispatcher = OutboxDispatcher(
        OutboxRepository(storage.unit_of_work),
        bus,
        owner_id="projection-replay-test",
        clock=lambda: clock[0],
        base_backoff_seconds=1,
        max_backoff_seconds=1,
    )
    original = storage.save_decree_if_absent

    def fail_projection(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("projection fault")

    monkeypatch.setattr(storage, "save_decree_if_absent", fail_projection)
    try:
        assert await dispatcher.drain_once() == 1
        record = service.get(requested.decision_request_id)
        assert record is not None and record.resolution is not None
        assert storage.list_decrees_by_memorial(memorial.id) == []
        status = storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM outbox_events WHERE event_type = 'decision.resolved'"
        ).fetchone()[0]
        assert status == "retry_wait"

        monkeypatch.setattr(storage, "save_decree_if_absent", original)
        clock[0] += timedelta(seconds=2)
        assert await dispatcher.drain_once() == 1
        assert await dispatcher.drain_once() == 0
        [decree] = storage.list_decrees_by_memorial(memorial.id)
        assert decree.id == requested.decision_request_id
        assert decree.action == "approve"
    finally:
        storage.close()


def test_tool_suspension_two_resolvers_have_one_generic_winner(tmp_path: Path) -> None:
    path = tmp_path / "tool-suspension-race.db"
    edict, memorial = _create_database(path)
    setup = Storage(str(path))
    setup.init_db()
    manager, _ = _manager(setup)
    requested = _suspend(manager, edict, memorial)
    setup.close()
    barrier = Barrier(2)

    def resolve(actor: str) -> tuple[str, str]:
        storage = Storage(str(path))
        storage.init_db()
        try:
            service = DecisionService(storage, clock=lambda: _NOW + timedelta(minutes=1))
            barrier.wait()
            try:
                resolution = service.resolve(
                    requested.decision_request_id,
                    _resolution(),
                    auth=_auth(actor, f"race:{actor}"),
                )
            except DecisionConflict as exc:
                return "conflict", exc.code
            return "resolved", resolution.actor_principal_id
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolve, ("user:first", "user:second")))

    assert len([result for result in results if result[0] == "resolved"]) == 1
    assert [result[1] for result in results if result[0] == "conflict"] == [
        "decision_already_resolved"
    ]
    verify = Storage(str(path))
    verify.init_db()
    try:
        assert verify._conn.execute("SELECT COUNT(*) FROM decision_resolutions").fetchone()[0] == 1  # noqa: SLF001
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'decision.resolved'"
            ).fetchone()[0]
            == 1
        )
    finally:
        verify.close()


async def test_concurrent_next_tool_suspensions_reject_stale_writer(tmp_path: Path) -> None:
    path = tmp_path / "next-tool-suspension-race.db"
    edict, memorial = _create_database(path)
    setup = Storage(str(path))
    setup.init_db()
    manager, service = _manager(setup)
    first = _suspend(manager, edict, memorial)
    service.resolve(
        first.decision_request_id,
        _resolution(),
        auth=_auth("user:reviewer", "next-tool:first-resolution"),
    )
    assert (
        await manager.wait_for_tool_decision(
            first.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )
        is not None
    )
    setup.close()
    barrier = Barrier(2)

    def suspend(invocation_id: str) -> tuple[str, str, str]:
        storage = Storage(str(path))
        storage.init_db()
        try:
            next_manager, _ = _manager(storage)
            barrier.wait()
            try:
                request = _suspend(
                    next_manager,
                    edict,
                    memorial,
                    invocation_id=invocation_id,
                    iteration=3,
                )
            except DecisionConflict as exc:
                return "conflict", invocation_id, exc.code
            return "waiting", invocation_id, request.decision_request_id
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(suspend, ("tool-call-next-a", "tool-call-next-b")))

    [winner] = [result for result in results if result[0] == "waiting"]
    assert [result[2] for result in results if result[0] == "conflict"] == [
        "decision_run_state_conflict"
    ]
    verify = Storage(str(path))
    verify.init_db()
    try:
        assert verify._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 2  # noqa: SLF001
        with verify.unit_of_work() as unit_of_work:
            state = verify.run_state_repo.load(unit_of_work.connection, memorial.id)
            unit_of_work.commit()
        assert state is not None and state.version == 3
        assert state.phase.value == "waiting_decision"
        assert state.continuation.pending_decision_id == winner[2]
        assert state.continuation.pending_tool is not None
        assert state.continuation.pending_tool.tool_call_id == winner[1]
    finally:
        verify.close()


def test_pending_tool_projection_survives_manager_restart(tmp_path: Path) -> None:
    path = tmp_path / "pending-tool-projection.db"
    edict, memorial = _create_database(path)
    storage = Storage(str(path))
    storage.init_db()
    try:
        manager, _ = _manager(storage)
        requested = _suspend(manager, edict, memorial)

        restarted, _ = _manager(storage)
        restarted._pending.clear()  # noqa: SLF001 - proves process maps are irrelevant
        restarted._pending_tool.clear()  # noqa: SLF001

        assert restarted.list_pending_tool_calls() == [
            {
                "decision_request_id": requested.decision_request_id,
                "memorial_id": memorial.id,
                "edict_id": edict.id,
                "tool_name": "write_file",
                "rule_id": "approval_required",
                "reason": None,
                "tool_tier": "T1_WORKSPACE",
                "args_summary": {"command": "git status", "path": "README.md"},
                "created_at": requested.created_at.isoformat(),
            }
        ]
    finally:
        storage.close()


async def test_canonical_resolver_is_idempotent_and_projects_one_session_rule(
    tmp_path: Path,
) -> None:
    path = tmp_path / "canonical-tool-resolver.db"
    edict, memorial = _create_database(path)
    storage = Storage(str(path))
    storage.init_db()
    rules = InMemorySessionRuleStore()
    service = DecisionService(storage, clock=lambda: _NOW)
    manager = ApprovalManager(
        EventBus(),
        storage,
        session_rule_store=rules,
        decision_service=service,
        clock=lambda: _NOW,
    )
    try:
        requested = _suspend(manager, edict, memorial)
        winner, replay = await asyncio.gather(
            manager.resolve_tool_decision(
                requested.decision_request_id,
                action="approve",
                grant_scope="edict",
                grant_reason="reviewed",
                auth=_auth("user:web", "web-wins"),
            ),
            manager.resolve_tool_decision(
                requested.decision_request_id,
                action="reject",
                comment="telegram loses",
                auth=_auth("telegram:7", "telegram-loses"),
            ),
        )

        assert replay == winner
        assert winner.resolution is not None
        assert winner.resolution.action == "approve"
        assert winner.resolution.actor_principal_id == "user:web"
        assert winner.resolution.payload["grant_scope"] == "edict"
        assert len(storage.list_decrees_by_memorial(memorial.id)) == 1
        created_rules = await rules.list_by_scope("edict", edict.id)
        assert len(created_rules) == 1
        assert created_rules[0].rule_id == f"{requested.decision_request_id}:session-rule"
        assert created_rules[0].reason == "reviewed"
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM events WHERE id = ?",
                (f"{requested.decision_request_id}:decree.approved",),
            ).fetchone()[0]
            == 1
        )
    finally:
        storage.close()


async def test_dangerous_always_scope_is_downgraded_before_durable_resolution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dangerous-scope.db"
    edict, memorial = _create_database(path)
    storage = Storage(str(path))
    storage.init_db()
    try:
        manager, service = _manager(storage)
        requested = _suspend(manager, edict, memorial, tool_name="shell_exec")

        record = await manager.resolve_tool_decision(
            requested.decision_request_id,
            action="approve",
            grant_scope="always",
            auth=_auth("user:reviewer", "dangerous-scope"),
        )

        assert record.resolution is not None
        assert record.resolution.payload["grant_scope"] == "once"
        assert service.get(requested.decision_request_id) == record
    finally:
        storage.close()
