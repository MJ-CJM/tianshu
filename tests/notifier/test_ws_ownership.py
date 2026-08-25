"""WebSocket outbound ownership filtering contracts."""

from __future__ import annotations

import json

import pytest

from tianshu.models import Edict
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.notifier.notifier import Notifier


class RecordingWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.messages: list[dict] = []

    async def send_text(self, data: str) -> None:
        if self.fail_send:
            raise RuntimeError("socket closed")
        self.messages.append(json.loads(data))


class ActionWebSocket(RecordingWebSocket):
    def __init__(self, action, *, fail_after_action: bool = False) -> None:
        super().__init__()
        self._action = action
        self._fail_after_action = fail_after_action

    async def send_text(self, data: str) -> None:
        self._action()
        if self._fail_after_action:
            raise RuntimeError("socket identity changed while sending")
        await super().send_text(data)


def _context(principal_id: str, *, admin: bool = False) -> AuthContext:
    scopes = frozenset({"api", "admin"} if admin else {"api"})
    return AuthContext(
        principal=Principal(
            id=principal_id,
            kind=PrincipalKind.SERVICE,
            display_name=principal_id,
            scopes=scopes,
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id=f"ws-{principal_id}",
    )


@pytest.mark.asyncio
async def test_task_event_is_sent_only_to_owner_and_admin(storage) -> None:
    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    storage.save_edict(Edict(id="edict-b", goal="other task", submitter="service:b"))
    notifier = Notifier(storage)
    owner_a = RecordingWebSocket()
    owner_b = RecordingWebSocket()
    admin = RecordingWebSocket()
    notifier.register_ws(owner_a, _context("service:a"))
    notifier.register_ws(owner_b, _context("service:b"))
    notifier.register_ws(admin, _context("service:admin", admin=True))

    event_a = {"type": "stream.delta", "edict_id": "edict-a", "text": "output a"}
    event_b = {"type": "stream.delta", "edict_id": "edict-b", "text": "output b"}
    await notifier.broadcast_ws(event_a)
    await notifier.broadcast_ws(event_b)

    assert owner_a.messages == [event_a]
    assert owner_b.messages == [event_b]
    assert admin.messages == [event_a, event_b]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {"type": "consultation.started", "consultation_id": "consultation-1"},
        {"type": "digest.daily", "summary": "system event"},
        {"type": "stream.delta", "edict_id": "", "text": "empty id"},
        {"type": "stream.delta", "edict_id": "   ", "text": "blank id"},
        {"type": "stream.delta", "edict_id": "missing", "text": "unknown task"},
        {"type": "stream.delta", "edict_id": "legacy", "text": "legacy task"},
        {"type": "stream.delta", "edict_id": "empty-owner", "text": "legacy task"},
        {"type": "stream.delta", "edict_id": "blank-owner", "text": "legacy task"},
    ],
)
async def test_event_without_known_owner_is_admin_only(storage, message: dict) -> None:
    storage.save_edict(Edict(id="legacy", goal="legacy task", submitter=None))
    storage.save_edict(Edict(id="empty-owner", goal="legacy task", submitter=""))
    storage.save_edict(Edict(id="blank-owner", goal="legacy task", submitter="   "))
    notifier = Notifier(storage)
    ordinary = RecordingWebSocket()
    admin = RecordingWebSocket()
    notifier.register_ws(ordinary, _context("service:a"))
    notifier.register_ws(admin, _context("service:admin", admin=True))

    await notifier.broadcast_ws(message)

    assert ordinary.messages == []
    assert admin.messages == [message]


@pytest.mark.asyncio
async def test_owner_lookup_failure_is_admin_only(storage, monkeypatch) -> None:
    notifier = Notifier(storage)
    ordinary = RecordingWebSocket()
    admin = RecordingWebSocket()
    notifier.register_ws(ordinary, _context("service:a"))
    notifier.register_ws(admin, _context("service:admin", admin=True))

    def fail_lookup(edict_id: str):
        raise RuntimeError(f"cannot read {edict_id}")

    monkeypatch.setattr(storage, "get_edict", fail_lookup)
    message = {"type": "stream.delta", "edict_id": "edict-a", "text": "private"}

    await notifier.broadcast_ws(message)

    assert ordinary.messages == []
    assert admin.messages == [message]


@pytest.mark.asyncio
async def test_owner_lookup_is_cached_for_stream_deltas(storage, monkeypatch) -> None:
    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    get_edict = storage.get_edict
    lookups: list[str] = []

    def counting_lookup(edict_id: str):
        lookups.append(edict_id)
        return get_edict(edict_id)

    monkeypatch.setattr(storage, "get_edict", counting_lookup)
    notifier = Notifier(storage)
    owner = RecordingWebSocket()
    notifier.register_ws(owner, _context("service:a"))

    await notifier.broadcast_ws({"type": "stream.delta", "edict_id": "edict-a", "text": "first"})
    await notifier.broadcast_ws({"type": "stream.delta", "edict_id": "edict-a", "text": "second"})

    assert lookups == ["edict-a"]
    assert [message["text"] for message in owner.messages] == ["first", "second"]


@pytest.mark.asyncio
async def test_dead_authorized_socket_is_removed_without_affecting_live_socket(storage) -> None:
    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    notifier = Notifier(storage)
    dead = RecordingWebSocket(fail_send=True)
    live = RecordingWebSocket()
    context = _context("service:a")
    notifier.register_ws(dead, context)
    notifier.register_ws(live, context)

    await notifier.broadcast_ws({"type": "stream.delta", "edict_id": "edict-a", "text": "private"})

    assert dead not in notifier._ws_clients
    assert live in notifier._ws_clients
    assert live.messages[0]["text"] == "private"


@pytest.mark.asyncio
async def test_authorized_event_remains_redacted(storage) -> None:
    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    notifier = Notifier(storage)
    owner = RecordingWebSocket()
    other = RecordingWebSocket()
    admin = RecordingWebSocket()
    notifier.register_ws(owner, _context("service:a"))
    notifier.register_ws(other, _context("service:b"))
    notifier.register_ws(admin, _context("service:admin", admin=True))

    await notifier.broadcast_ws(
        {
            "type": "stream.delta",
            "edict_id": "edict-a",
            "text": "API_KEY=supersecretvalue123",
        }
    )

    assert owner.messages[0]["text"] == "[REDACTED CREDENTIAL]"
    assert other.messages == []
    assert admin.messages == owner.messages


@pytest.mark.asyncio
async def test_missing_edict_is_rechecked_after_it_is_created(storage) -> None:
    notifier = Notifier(storage)
    owner = RecordingWebSocket()
    notifier.register_ws(owner, _context("service:a"))
    event = {"type": "stream.delta", "edict_id": "edict-a", "text": "private"}

    await notifier.broadcast_ws(event)
    assert owner.messages == []
    assert "edict-a" not in notifier._ws_edict_submitters

    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    await notifier.broadcast_ws(event)

    assert owner.messages == [event]


@pytest.mark.asyncio
async def test_failed_lookup_is_retried_after_storage_recovers(storage, monkeypatch) -> None:
    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    get_edict = storage.get_edict
    attempts = 0

    def flaky_lookup(edict_id: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("storage unavailable")
        return get_edict(edict_id)

    monkeypatch.setattr(storage, "get_edict", flaky_lookup)
    notifier = Notifier(storage)
    owner = RecordingWebSocket()
    notifier.register_ws(owner, _context("service:a"))
    event = {"type": "stream.delta", "edict_id": "edict-a", "text": "private"}

    await notifier.broadcast_ws(event)
    assert owner.messages == []
    assert "edict-a" not in notifier._ws_edict_submitters

    await notifier.broadcast_ws(event)

    assert attempts == 2
    assert owner.messages == [event]


@pytest.mark.asyncio
async def test_stale_snapshot_context_cannot_receive_owner_event(storage) -> None:
    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    notifier = Notifier(storage)
    stale = RecordingWebSocket()
    context_b = _context("service:b")
    trigger = ActionWebSocket(lambda: notifier.register_ws(stale, context_b))
    context_a = _context("service:a")
    notifier.register_ws(trigger, context_a)
    notifier.register_ws(stale, context_a)

    await notifier.broadcast_ws({"type": "stream.delta", "edict_id": "edict-a", "text": "private"})

    assert trigger.messages[0]["text"] == "private"
    assert stale.messages == []
    assert notifier._ws_clients[stale] is context_b


@pytest.mark.asyncio
async def test_failed_send_does_not_remove_reregistered_socket(storage) -> None:
    storage.save_edict(Edict(id="edict-a", goal="owner task", submitter="service:a"))
    notifier = Notifier(storage)
    context_b = _context("service:b")
    changing = None

    def reregister() -> None:
        assert changing is not None
        notifier.register_ws(changing, context_b)

    changing = ActionWebSocket(reregister, fail_after_action=True)
    notifier.register_ws(changing, _context("service:a"))

    await notifier.broadcast_ws({"type": "stream.delta", "edict_id": "edict-a", "text": "private"})

    assert notifier._ws_clients[changing] is context_b
