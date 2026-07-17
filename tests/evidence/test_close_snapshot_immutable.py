from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.evidence.service import EvidenceImportError
from tianshu.governance.decision_service import DecisionService
from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.decision import (
    DecisionKind,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.principal import AuthContext, Principal

from ._fixtures import evidence_service, seed_closed_run


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="local:owner",
            kind="local",
            display_name="Local Owner",
            scopes=frozenset({"*"}),
        ),
        source="trusted-local",
        client_kind="web",
        correlation_id="corr-evidence-decision",
    )


def test_close_is_canonical_idempotent_immutable_and_independently_verifiable(
    storage, tmp_path
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")

    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)

    assert isinstance(closed, ClosedEvidenceBundleV1)
    assert service.export(closed.bundle_id) == canonical_json_bytes(closed)
    assert service.close(memorial.id, expected_version=opened.version) == closed
    assert service.verify(closed.bundle_id).verified
    assert service.verify_export(service.export(closed.bundle_id)).verified
    schema = json.loads(
        (
            Path(__file__).parents[2] / "docs" / "reference" / "evidence-bundle-v1.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(service.export(closed.bundle_id)))

    for statement in (
        "UPDATE evidence_bundles SET body_json='{}' WHERE bundle_id=?",
        "DELETE FROM evidence_bundles WHERE bundle_id=?",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            storage._conn.execute(statement, (closed.bundle_id,))  # noqa: SLF001
        storage._conn.rollback()  # noqa: SLF001

    tampered = json.loads(service.export(closed.bundle_id))
    tampered["snapshot"]["cost"]["prompt_tokens"] += 1
    verification = service.verify_export(canonical_json_bytes(tampered))
    assert not verification.verified
    assert "content_hash_mismatch" in verification.reason_codes

    other_root = evidence_service(storage, tmp_path / "other-root")
    with pytest.raises(EvidenceImportError, match="artifact_invalid"):
        other_root.import_bundle(service.export(closed.bundle_id))


def test_two_closers_cannot_create_distinct_snapshots(storage, tmp_path) -> None:
    _, memorial = seed_closed_run(storage)
    first = evidence_service(storage, tmp_path / "artifacts")
    second = evidence_service(storage, tmp_path / "artifacts")
    opened = first.build_open(memorial.id)

    winner = first.close(memorial.id, expected_version=opened.version)
    loser_retry = second.close(memorial.id, expected_version=opened.version)

    assert loser_retry == winner
    rows = storage._conn.execute(  # noqa: SLF001
        "SELECT body_json, content_hash FROM evidence_bundles WHERE memorial_id=?",
        (memorial.id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["body_json"] == canonical_json_bytes(winner).decode()


def test_close_decodes_a_durable_resolution_timestamp_from_sqlite(storage, tmp_path) -> None:
    edict, memorial = seed_closed_run(storage)
    now = datetime(2026, 7, 17, 8, 12, tzinfo=UTC)
    decisions = DecisionService(storage, clock=lambda: now)
    requested = decisions.request(
        RequestDecisionCommand(
            kind=DecisionKind.PLAN_REVIEW,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key="plan:evidence-regression",
            payload={"plan_hash": "a" * 64},
            expires_at=now + timedelta(minutes=10),
        ),
        auth=_auth(),
    )
    decisions.resolve(
        requested.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="reviewed for immutable evidence",
            payload={"schema_version": 1},
            expected_version=requested.version,
        ),
        auth=_auth(),
    )
    service = evidence_service(storage, tmp_path / "artifacts")

    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)

    assert closed.snapshot.decisions[0].resolved_at == now
