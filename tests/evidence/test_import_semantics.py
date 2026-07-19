from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from tianshu.evidence.models import closed_bundle_content_hash
from tianshu.evidence.service import EvidenceImportError
from tianshu.models.canonical import canonical_json_bytes

from ._fixtures import evidence_service, seed_closed_run


def _closed_payload(storage, tmp_path):
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)
    return service, json.loads(service.export(closed.bundle_id))


def _required_evidence(requirements: dict[str, list[str]]) -> list[str]:
    return (
        [f"check:{value}" for value in sorted(set(requirements["check_names"]))]
        + [f"decision:{value}" for value in sorted(set(requirements["decision_request_ids"]))]
        + [f"effect:{value}" for value in sorted(set(requirements["effect_intent_ids"]))]
        + [f"artifact:{value}" for value in sorted(set(requirements["artifact_digests"]))]
    )


def _signed_export(payload: dict[str, object]) -> bytes:
    payload["content_hash"] = closed_bundle_content_hash(payload)
    return canonical_json_bytes(payload)


@pytest.mark.parametrize(
    ("requirements_field", "missing_value", "missing_ref"),
    [
        ("check_names", "missing-check", "check:missing-check"),
        ("decision_request_ids", "decision-missing", "decision:decision-missing"),
        ("effect_intent_ids", "c" * 64, f"effect:{'c' * 64}"),
        ("artifact_digests", "f" * 64, f"artifact:{'f' * 64}"),
    ],
)
def test_recomputed_hash_cannot_hide_missing_required_evidence(
    storage,
    tmp_path,
    requirements_field: str,
    missing_value: str,
    missing_ref: str,
) -> None:
    service, payload = _closed_payload(storage, tmp_path)
    requirements = payload["snapshot"]["requirements"]
    requirements[requirements_field].append(missing_value)
    payload["snapshot"]["auditor"]["required_evidence"] = _required_evidence(requirements)
    exported = _signed_export(payload)

    verification = service.verify_export(exported)

    assert not verification.verified
    assert f"missing_required:{missing_ref}" in verification.reason_codes
    with pytest.raises(EvidenceImportError, match="missing_required"):
        service.import_bundle(exported)


@pytest.mark.parametrize(
    ("mutate_auditor", "expected_reason"),
    [
        (
            lambda auditor: auditor.update(verdict="fail"),
            "auditor_verdict_mismatch",
        ),
        (
            lambda auditor: auditor.update(required_evidence=[]),
            "auditor_required_evidence_mismatch",
        ),
        (
            lambda auditor: auditor.update(missing_evidence=["check:phantom"]),
            "auditor_missing_evidence_mismatch",
        ),
    ],
)
def test_recomputed_hash_cannot_hide_contradictory_auditor_conclusion(
    storage,
    tmp_path,
    mutate_auditor: Callable[[dict[str, object]], None],
    expected_reason: str,
) -> None:
    service, payload = _closed_payload(storage, tmp_path)
    mutate_auditor(payload["snapshot"]["auditor"])
    exported = _signed_export(payload)

    verification = service.verify_export(exported)

    assert not verification.verified
    assert expected_reason in verification.reason_codes
    with pytest.raises(EvidenceImportError, match=expected_reason):
        service.import_bundle(exported)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("digest", "f" * 64),
        ("root_fingerprint", "e" * 64),
        ("media_type", "text/plain"),
        ("redaction", "reviewed"),
        ("size_bytes", 1_000_000),
    ],
)
def test_exported_artifact_metadata_must_exactly_match_durable_record(
    storage,
    tmp_path,
    field: str,
    replacement: str | int,
) -> None:
    service, payload = _closed_payload(storage, tmp_path)
    artifact = payload["snapshot"]["artifacts"][0]
    artifact[field] = replacement
    if field == "digest":
        artifact["uri"] = f"artifact://sha256/{replacement}"
    exported = _signed_export(payload)

    verification = service.verify_export(exported)

    assert not verification.verified
    assert any(reason.startswith("artifact_invalid:") for reason in verification.reason_codes)
    with pytest.raises(EvidenceImportError, match="artifact_invalid"):
        service.import_bundle(exported)
