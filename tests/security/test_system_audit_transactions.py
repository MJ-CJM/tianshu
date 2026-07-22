"""Atomic SystemAudit coverage for security-sensitive state transitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from threading import Event, Thread

import pytest

from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.security.estop import EstopManager
from tianshu.storage import Storage

_ACTOR_DIGEST = "a" * 64


def _settings() -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        auth_bootstrap_token_hash=(
            "sha256:" + hashlib.sha256(b"bootstrap-token-for-system-audit-tests").hexdigest()
        ),
    )


def _principal(*, suffix: str = "owner") -> Principal:
    return Principal(
        id=f"user:{suffix}",
        kind=PrincipalKind.HUMAN,
        display_name=f"Owner {suffix}",
        scopes=frozenset({"admin", "api", "mcp:read"}),
    )


def _audit_context(correlation_id: str):
    from tianshu.gateway.auth import SecurityAuditContext

    return SecurityAuditContext(
        correlation_id=correlation_id,
        actor_digest=_ACTOR_DIGEST,
    )


def _raising_audit(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("injected audit failure")


def _token_rows(storage: Storage) -> list[dict]:
    return storage.list_auth_tokens()


def test_pat_issue_rotate_and_revoke_append_redacted_correlated_events(
    storage: Storage,
) -> None:
    service = AuthService(storage, _settings())
    principal = _principal()

    issued = service.issue_pat(
        principal,
        label="audit-test",
        scopes=frozenset({"api"}),
        audit_context=_audit_context("pat-issue-correlation"),
    )
    rotated = service.rotate_pat(
        issued.id,
        audit_context=_audit_context("pat-rotate-correlation"),
    )
    assert service.revoke_token(
        rotated.id,
        audit_context=_audit_context("pat-revoke-correlation"),
    )

    events = storage.list_system_audit()
    assert [event.action for event in events] == [
        "auth.token.issued",
        "auth.token.rotated",
        "auth.token.revoked",
    ]
    assert [event.correlation_id for event in events] == [
        "pat-issue-correlation",
        "pat-rotate-correlation",
        "pat-revoke-correlation",
    ]
    assert [event.metadata["token_type"] for event in events] == ["pat", "pat", "pat"]
    assert all(event.actor_digest == _ACTOR_DIGEST for event in events)
    serialized = repr([event.model_dump(mode="json") for event in events])
    for forbidden in (
        principal.id,
        principal.display_name,
        issued.id,
        issued.raw_token,
        rotated.id,
        rotated.raw_token,
    ):
        assert forbidden not in serialized


def test_session_create_rotate_revoke_and_denials_are_audited(storage: Storage) -> None:
    service = AuthService(storage, _settings())
    pat = service.issue_pat(
        _principal(),
        label="web-login",
        scopes=frozenset({"admin", "api"}),
        audit_context=_audit_context("session-pat-correlation"),
    )

    first = service.create_session(
        pat.raw_token,
        audit_context=_audit_context("session-create-correlation"),
    )
    assert first is not None
    refreshed = service.refresh_session(
        first.refresh_token,
        audit_context=_audit_context("session-rotate-correlation"),
    )
    assert refreshed is not None
    assert service.revoke_session(
        next(
            row["id"]
            for row in _token_rows(storage)
            if row["family_id"] == refreshed.family_id and row["token_type"] == "access"
        ),
        audit_context=_audit_context("session-revoke-correlation"),
    )
    assert (
        service.create_session(
            "SECRET_DENIED_SESSION_SENTINEL",
            audit_context=_audit_context("session-create-denied-correlation"),
        )
        is None
    )
    assert (
        service.refresh_session(
            first.refresh_token,
            audit_context=_audit_context("session-refresh-denied-correlation"),
        )
        is None
    )

    events = storage.list_system_audit()
    actions = [event.action for event in events]
    assert actions.count("auth.token.issued") == 3
    assert actions.count("auth.session.rotated") == 1
    assert actions.count("auth.session.revoked") == 1
    assert actions.count("auth.session.denied") == 2
    created = [event for event in events if event.correlation_id == "session-create-correlation"]
    assert [event.metadata["token_type"] for event in created] == ["access", "refresh"]
    assert all(event.outcome == "succeeded" for event in created)
    denied = [event for event in events if event.action == "auth.session.denied"]
    assert {event.correlation_id for event in denied} == {
        "session-create-denied-correlation",
        "session-refresh-denied-correlation",
    }
    assert all(event.outcome == "denied" for event in denied)
    serialized = repr([event.model_dump(mode="json") for event in events])
    assert "SECRET_DENIED_SESSION_SENTINEL" not in serialized
    assert pat.raw_token not in serialized
    assert first.refresh_token not in serialized


def test_repeated_pat_revoke_returns_false_without_duplicate_success_audit(
    storage: Storage,
) -> None:
    service = AuthService(storage, _settings())
    issued = service.issue_pat(
        _principal(suffix="repeat-pat"),
        label="repeat-pat",
        scopes=frozenset({"api"}),
        audit_context=_audit_context("repeat-pat-issue"),
    )

    assert service.revoke_token(
        issued.id,
        audit_context=_audit_context("repeat-pat-first-revoke"),
    )
    assert not service.revoke_token(
        issued.id,
        audit_context=_audit_context("repeat-pat-second-revoke"),
    )

    revoked = [
        event for event in storage.list_system_audit() if event.action == "auth.token.revoked"
    ]
    assert len(revoked) == 1
    assert revoked[0].correlation_id == "repeat-pat-first-revoke"
    assert revoked[0].metadata["family_size"] == 1


def test_repeated_session_revoke_returns_false_and_uses_transitioned_family_size(
    storage: Storage,
) -> None:
    service = AuthService(storage, _settings())
    pat = service.issue_pat(
        _principal(suffix="repeat-session"),
        label="repeat-session",
        scopes=frozenset({"api"}),
        audit_context=_audit_context("repeat-session-pat"),
    )
    pair = service.create_session(
        pat.raw_token,
        audit_context=_audit_context("repeat-session-create"),
    )
    assert pair is not None
    credential_id = next(
        row["id"]
        for row in _token_rows(storage)
        if row["family_id"] == pair.family_id and row["token_type"] == "access"
    )

    assert service.revoke_session(
        credential_id,
        audit_context=_audit_context("repeat-session-first-revoke"),
    )
    assert not service.revoke_session(
        credential_id,
        audit_context=_audit_context("repeat-session-second-revoke"),
    )

    revoked = [
        event for event in storage.list_system_audit() if event.action == "auth.session.revoked"
    ]
    assert len(revoked) == 1
    assert revoked[0].correlation_id == "repeat-session-first-revoke"
    assert revoked[0].metadata["family_size"] == 2


def test_unattributed_direct_mutations_use_internal_actor_not_target_owner(
    storage: Storage,
) -> None:
    service = AuthService(storage, _settings())
    principal = _principal(suffix="unattributed-target")
    target_digest = hashlib.sha256(principal.id.encode()).hexdigest()
    internal_literal = "internal-legacy-unattributed-caller"
    internal_digest = hashlib.sha256(internal_literal.encode()).hexdigest()

    rotate_target = service.issue_pat(
        principal,
        label="rotate-target",
        scopes=frozenset({"api"}),
        audit_context=_audit_context("unattributed-rotate-setup"),
    )
    service.rotate_pat(rotate_target.id)

    revoke_target = service.issue_pat(
        principal,
        label="revoke-target",
        scopes=frozenset({"api"}),
        audit_context=_audit_context("unattributed-revoke-setup"),
    )
    assert service.revoke_token(revoke_target.id)

    session_pat = service.issue_pat(
        principal,
        label="session-target",
        scopes=frozenset({"api"}),
        audit_context=_audit_context("unattributed-session-pat"),
    )
    pair = service.create_session(
        session_pat.raw_token,
        audit_context=_audit_context("unattributed-session-create"),
    )
    assert pair is not None
    credential_id = next(
        row["id"]
        for row in _token_rows(storage)
        if row["family_id"] == pair.family_id and row["token_type"] == "access"
    )
    assert service.revoke_session(credential_id)

    actions = {"auth.token.rotated", "auth.token.revoked", "auth.session.revoked"}
    direct_events = [event for event in storage.list_system_audit() if event.action in actions]
    assert len(direct_events) == 3
    assert all(event.actor_digest == internal_digest for event in direct_events)
    assert all(event.actor_digest != target_digest for event in direct_events)
    assert internal_literal not in repr([event.model_dump(mode="json") for event in direct_events])


@pytest.mark.parametrize(
    "mutation",
    ["issue", "rotate", "revoke"],
)
def test_pat_state_rolls_back_when_audit_append_fails(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    service = AuthService(storage, _settings())
    principal = _principal(suffix=mutation)
    issued = None
    if mutation != "issue":
        issued = service.issue_pat(
            principal,
            label="before-failure",
            scopes=frozenset({"api"}),
            audit_context=_audit_context(f"pat-{mutation}-setup"),
        )
    before = _token_rows(storage)

    import tianshu.storage.auth_repo as auth_repo

    monkeypatch.setattr(auth_repo, "_append_system_audit_unlocked", _raising_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        if mutation == "issue":
            service.issue_pat(
                principal,
                label="failed-issue",
                scopes=frozenset({"api"}),
                audit_context=_audit_context("failed-pat-issue"),
            )
        elif mutation == "rotate":
            assert issued is not None
            service.rotate_pat(
                issued.id,
                audit_context=_audit_context("failed-pat-rotate"),
            )
        else:
            assert issued is not None
            service.revoke_token(
                issued.id,
                audit_context=_audit_context("failed-pat-revoke"),
            )

    assert _token_rows(storage) == before


@pytest.mark.parametrize(
    "mutation",
    ["create", "rotate", "revoke", "replay-denial"],
)
def test_session_family_rolls_back_when_audit_append_fails(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    service = AuthService(storage, _settings())
    pat = service.issue_pat(
        _principal(suffix=mutation),
        label="session-setup",
        scopes=frozenset({"api"}),
        audit_context=_audit_context(f"session-{mutation}-pat-setup"),
    )
    first = None
    refreshed = None
    if mutation != "create":
        first = service.create_session(
            pat.raw_token,
            audit_context=_audit_context(f"session-{mutation}-create-setup"),
        )
    if mutation == "replay-denial":
        assert first is not None
        refreshed = service.refresh_session(
            first.refresh_token,
            audit_context=_audit_context("session-replay-rotate-setup"),
        )
        assert refreshed is not None
    before = _token_rows(storage)

    import tianshu.storage.auth_repo as auth_repo

    monkeypatch.setattr(auth_repo, "_append_system_audit_unlocked", _raising_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        if mutation == "create":
            service.create_session(
                pat.raw_token,
                audit_context=_audit_context("failed-session-create"),
            )
        elif mutation == "rotate":
            assert first is not None
            service.refresh_session(
                first.refresh_token,
                audit_context=_audit_context("failed-session-rotate"),
            )
        elif mutation == "revoke":
            assert first is not None
            credential_id = next(
                row["id"]
                for row in before
                if row["family_id"] == first.family_id and row["token_type"] == "access"
            )
            service.revoke_session(
                credential_id,
                audit_context=_audit_context("failed-session-revoke"),
            )
        else:
            assert first is not None and refreshed is not None
            service.refresh_session(
                first.refresh_token,
                audit_context=_audit_context("failed-session-replay-denial"),
            )

    assert _token_rows(storage) == before


def test_estop_engage_and_resume_append_audit_after_atomic_state_commit(
    storage: Storage,
) -> None:
    manager = EstopManager(storage)
    manager.engage(
        kill_all=True,
        freeze_tools=["shell_exec"],
        reason="drill",
        audit_context=_audit_context("estop-engage-correlation"),
    )
    manager.resume(
        all_clear=True,
        audit_context=_audit_context("estop-resume-correlation"),
    )

    events = storage.list_system_audit()
    assert [event.action for event in events] == ["estop.engaged", "estop.resumed"]
    assert [event.correlation_id for event in events] == [
        "estop-engage-correlation",
        "estop-resume-correlation",
    ]
    assert manager.status().engaged is False
    assert storage.get_estop_state()["kill_all"] == 0


@pytest.mark.parametrize(
    ("prepare", "mutate"),
    [
        (
            lambda manager: None,
            lambda manager, context: manager.engage(
                kill_all=True,
                audit_context=context,
            ),
        ),
        (
            lambda manager: manager.engage(kill_all=True),
            lambda manager, context: manager.resume(
                all_clear=True,
                audit_context=context,
            ),
        ),
    ],
    ids=["engage", "resume"],
)
def test_estop_database_and_memory_roll_back_when_audit_append_fails(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[EstopManager], None],
    mutate: Callable[[EstopManager, object], None],
) -> None:
    manager = EstopManager(storage)
    prepare(manager)
    before_memory = manager.status()
    before_database = storage.get_estop_state()

    import tianshu.storage.security_repo as security_repo

    monkeypatch.setattr(security_repo, "_append_system_audit_unlocked", _raising_audit)
    context = _audit_context("failed-estop-correlation")
    with pytest.raises(RuntimeError, match="injected audit failure"):
        mutate(manager, context)

    assert manager.status() == before_memory
    assert storage.get_estop_state() == before_database


def test_estop_concurrent_transitions_keep_cache_database_and_last_audit_consistent(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = EstopManager(storage)
    engage_committed = Event()
    release_engage = Event()
    original_save = storage.save_estop_state_with_audit

    def controlled_save(state: dict, audit: AppendSystemAuditRequest) -> None:
        original_save(state, audit)
        if audit.action == "estop.engaged":
            engage_committed.set()
            assert release_engage.wait(timeout=5)

    monkeypatch.setattr(storage, "save_estop_state_with_audit", controlled_save)
    errors: list[BaseException] = []

    def run(call: Callable[[], object]) -> None:
        try:
            call()
        except BaseException as exc:  # noqa: BLE001 - thread failures must fail the test
            errors.append(exc)

    engage_thread = Thread(
        target=run,
        args=(
            lambda: manager.engage(
                kill_all=True,
                audit_context=_audit_context("concurrent-estop-engage"),
            ),
        ),
    )
    engage_thread.start()
    assert engage_committed.wait(timeout=5)

    resume_thread = Thread(
        target=run,
        args=(
            lambda: manager.resume(
                all_clear=True,
                audit_context=_audit_context("concurrent-estop-resume"),
            ),
        ),
    )
    resume_thread.start()
    resume_thread.join(timeout=1)
    release_engage.set()
    engage_thread.join(timeout=5)
    resume_thread.join(timeout=5)

    assert not engage_thread.is_alive()
    assert not resume_thread.is_alive()
    assert errors == []
    state = manager.status()
    row = storage.get_estop_state()
    assert row is not None
    last_audit = storage.list_system_audit()[-1]
    assert last_audit.action == "estop.resumed"
    assert state.kill_all is bool(row["kill_all"]) is last_audit.metadata["kill_all"]
    assert state.network_kill is bool(row["network_kill"]) is last_audit.metadata["network_kill"]
    assert state.frozen_tools == frozenset(json.loads(row["frozen_tools_json"]))
    assert len(state.frozen_tools) == last_audit.metadata["frozen_tool_count"]
