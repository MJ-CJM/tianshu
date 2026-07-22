"""Real-file restart and separate-connection races for durable decisions."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

from tianshu.governance.decision_service import DecisionConflict, DecisionService
from tianshu.models.decision import DecisionKind, RequestDecisionCommand, ResolveDecisionCommand
from tianshu.models.principal import AuthContext, Principal
from tianshu.storage import Storage

_NOW = datetime(2026, 7, 15, 9, tzinfo=UTC)


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


def _command(*, request_key: str = "tool-call:restart", tool_name: str = "read_file"):
    return RequestDecisionCommand(
        kind=DecisionKind.TOOL,
        edict_id="edict-1",
        memorial_id="memorial-1",
        request_key=request_key,
        payload={"tool_name": tool_name, "arguments": {"path": "README.md"}},
        expires_at=_NOW + timedelta(minutes=10),
    )


def _resolution() -> ResolveDecisionCommand:
    return ResolveDecisionCommand(
        action="approve",
        reason="independent review complete",
        payload={"schema_version": 1, "grant_scope": "once", "grant_reason": None},
        expected_version=1,
    )


def _create_database(path: Path) -> None:
    storage = Storage(str(path))
    storage.init_db()
    storage._conn.execute(  # noqa: SLF001 - real-file authority fixture
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-1", "goal", _NOW.isoformat()),
    )
    storage._conn.execute(  # noqa: SLF001 - real-file authority fixture
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-1", "edict-1", "submitted", _NOW.isoformat()),
    )
    storage._conn.commit()  # noqa: SLF001
    storage.close()


def test_pending_decision_survives_restart_and_resolves_from_new_service(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    _create_database(path)
    first = Storage(str(path))
    first.init_db()
    requested = DecisionService(first, clock=lambda: _NOW).request(
        _command(),
        auth=_auth("user:requester", "restart-request"),
    )
    first.close()

    restarted = Storage(str(path))
    restarted.init_db()
    try:
        service = DecisionService(restarted, clock=lambda: _NOW + timedelta(minutes=1))
        assert service.get(requested.decision_request_id).request == requested
        assert service.list_pending() == [requested]
        resolved = service.resolve(
            requested.decision_request_id,
            _resolution(),
            auth=_auth("user:reviewer", "restart-resolve"),
        )
        assert resolved.actor_principal_id == "user:reviewer"
        assert (
            restarted._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'decision.resolved'"
            ).fetchone()[0]
            == 1
        )
    finally:
        restarted.close()


def test_two_connection_request_identity_race_has_one_authority_and_canonical_denial_subject(
    tmp_path: Path,
) -> None:
    path = tmp_path / "request-race.db"
    _create_database(path)
    barrier = Barrier(2)

    def request(tool_name: str, actor: str) -> tuple[str, str]:
        storage = Storage(str(path))
        storage.init_db()
        try:
            service = DecisionService(storage, clock=lambda: _NOW)
            barrier.wait()
            try:
                saved = service.request(
                    _command(request_key="tool-call:race", tool_name=tool_name),
                    auth=_auth(actor, f"request-race:{actor}"),
                )
            except DecisionConflict as exc:
                return "conflict", exc.code
            return "saved", saved.decision_request_id
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: request(*args),
                (("read_file", "user:first"), ("write_file", "user:second")),
            )
        )

    [winner] = [value for outcome, value in results if outcome == "saved"]
    assert [value for outcome, value in results if outcome == "conflict"] == [
        "decision_identity_conflict"
    ]
    verify = Storage(str(path))
    verify.init_db()
    try:
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM decision_requests WHERE request_key = 'tool-call:race'"
            ).fetchone()[0]
            == 1
        )
        [denial] = [
            event
            for event in verify.list_system_audit()
            if event.action == "decision.request.denied"
        ]
        assert denial.subject_digest == hashlib.sha256(winner.encode()).hexdigest()
    finally:
        verify.close()


def test_two_connection_same_payload_request_race_deduplicates_to_one_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "request-dedupe-race.db"
    _create_database(path)
    barrier = Barrier(2)

    def request(actor: str) -> str:
        storage = Storage(str(path))
        storage.init_db()
        try:
            service = DecisionService(storage, clock=lambda: _NOW)
            barrier.wait()
            return service.request(
                _command(request_key="tool-call:same-payload-race"),
                auth=_auth(actor, f"request-dedupe-race:{actor}"),
            ).decision_request_id
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        decision_ids = list(pool.map(request, ("user:first", "user:second")))

    assert len(set(decision_ids)) == 1
    verify = Storage(str(path))
    verify.init_db()
    try:
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM decision_requests WHERE request_key = ?",
                ("tool-call:same-payload-race",),
            ).fetchone()[0]
            == 1
        )
        assert [
            event
            for event in verify.list_system_audit()
            if event.action == "decision.request.denied"
        ] == []
    finally:
        verify.close()


def test_two_connection_resolve_race_has_one_resolution_and_one_outbox(tmp_path: Path) -> None:
    path = tmp_path / "resolve-race.db"
    _create_database(path)
    setup = Storage(str(path))
    setup.init_db()
    requested = DecisionService(setup, clock=lambda: _NOW).request(
        _command(request_key="tool-call:resolve-race"),
        auth=_auth("user:requester", "resolve-race-request"),
    )
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
                    auth=_auth(actor, f"resolve-race:{actor}"),
                )
            except DecisionConflict as exc:
                return "conflict", exc.code
            return "resolved", resolution.actor_principal_id
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolve, ("user:first", "user:second")))

    [winner] = [value for outcome, value in results if outcome == "resolved"]
    assert [value for outcome, value in results if outcome == "conflict"] == [
        "decision_already_resolved"
    ]
    verify = Storage(str(path))
    verify.init_db()
    try:
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT actor_principal_id FROM decision_resolutions"
            ).fetchone()[0]
            == winner
        )
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM decision_resolutions"
            ).fetchone()[0]
            == 1
        )
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'decision.resolved'"
            ).fetchone()[0]
            == 1
        )
    finally:
        verify.close()
