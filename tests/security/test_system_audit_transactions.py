"""Atomic SystemAudit coverage for security-sensitive state transitions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest

from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService
from tianshu.models.principal import Principal, PrincipalKind
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
