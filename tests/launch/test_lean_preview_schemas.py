from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tianshu.models.canonical import canonical_sha256

_ROOT = Path(__file__).parents[2]
_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64
_STEP_IDS = (
    "doctor_ready",
    "submit_governed_edict",
    "observe_decision_required",
    "resolve_decision_with_reason",
    "observe_completed_run",
    "verify_evidence_bundle",
    "propose_skill_candidate",
    "evaluate_candidate_gate",
    "start_skill_canary",
    "submit_canary_eligible_run",
    "verify_real_candidate_overlay",
    "rollback_candidate",
    "verify_new_run_uses_champion",
)
_PHASE_REPORT_IDS = (
    "s1_g1_5",
    "s2_lean",
    "s3_core",
    "s4_automation",
    "s5_lean_core",
)
_DEFERRED_WORK_IDS = (
    "P2-A1",
    "P2-A2",
    "P2-A3",
    "P2-A4",
    "P2-A5",
    "P2-B1",
    "P2-B2",
    "P2-C1",
    "P2-C2",
    "P2-C3",
    "P3-D1",
    "P3-D2",
    "P3-D3",
    "P3-D4",
    "P4-E1",
    "P4-E2",
    "P4-E3",
    "P4-E4",
    "P4-E5",
)


def _module():
    import tianshu.models.lean_preview as lean_preview

    return lean_preview


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(payload)
    result.pop("content_hash", None)
    result["content_hash"] = canonical_sha256(result)
    return result


def _demo_payload() -> dict[str, object]:
    steps = []
    for index, step_id in enumerate(_STEP_IDS):
        steps.append(
            {
                "step_id": step_id,
                "status": "passed",
                "started_at": f"2026-07-18T00:00:{index:02d}Z",
                "completed_at": f"2026-07-18T00:00:{index + 1:02d}Z",
                "evidence_hashes": [_DIGEST],
                "observed_state_hash": _OTHER_DIGEST,
            }
        )
    return _rehash(
        {
            "schema_version": 1,
            "batch_id": "lean-preview-20260718-0001",
            "source_commit": "1" * 40,
            "wheel_sha256": _DIGEST,
            "environment_fingerprint": _OTHER_DIGEST,
            "fixture": False,
            "steps": steps,
            "evidence_bundle_id": "evidence:lean-preview-0001",
            "evidence_bundle_hash": _DIGEST,
            "candidate_id": "candidate:lean-preview-0001",
            "gate_hash": _OTHER_DIGEST,
            "assignment_id": "assignment:lean-preview-0001",
            "rollback_receipt_hash": _DIGEST,
            "external_pending": ["managed_openhands", "voiceover"],
        }
    )


def _candidate_payload() -> dict[str, object]:
    return _rehash(
        {
            "schema_version": 1,
            "source_commit": "1" * 40,
            "gate_evidence_ref": "evidence/gates/batch/manifest.json",
            "gate_evidence_hash": _DIGEST,
            "build_provenance_ref": "evidence/builds/batch/provenance.json",
            "build_provenance_hash": _OTHER_DIGEST,
            "phase_report_hashes": dict.fromkeys(_PHASE_REPORT_IDS, _DIGEST),
            "demo_report_ref": "demo-report.json",
            "demo_report_hash": _demo_payload()["content_hash"],
            "wheel_sha256": _DIGEST,
            "sdist_sha256": _OTHER_DIGEST,
            "capability_matrix_hash": _DIGEST,
            "automation_status": "passed",
            "visual_status": "user_approval_pending",
            "visual_approval_record_ref": None,
            "visual_approval_record_hash": None,
            "publication_status": "not_authorized",
            "deferred_work_ids": list(_DEFERRED_WORK_IDS),
        }
    )


def test_candidate_contract_requires_tracked_gate_and_build_provenance() -> None:
    module = _module()
    payload = _candidate_payload()
    payload.update(
        {
            "gate_evidence_ref": "evidence/gates/batch/manifest.json",
            "gate_evidence_hash": _DIGEST,
            "build_provenance_ref": "evidence/builds/batch/provenance.json",
            "build_provenance_hash": _OTHER_DIGEST,
        }
    )
    payload = _rehash(payload)

    model = _validate_json(module.LeanPreviewCandidateReportV1, payload)
    assert model.gate_evidence_hash == _DIGEST
    assert model.build_provenance_hash == _OTHER_DIGEST
    assert not _schema_errors("lean-preview-candidate-report-v1.schema.json", payload)

    for field in (
        "gate_evidence_ref",
        "gate_evidence_hash",
        "build_provenance_ref",
        "build_provenance_hash",
    ):
        missing = _rehash({key: value for key, value in payload.items() if key != field})
        with pytest.raises(ValidationError, match=field):
            _validate_json(module.LeanPreviewCandidateReportV1, missing)
        assert _schema_errors("lean-preview-candidate-report-v1.schema.json", missing)


def _approval_payload(*, demo_report_hash: str | None = None) -> dict[str, object]:
    return _rehash(
        {
            "schema_version": 1,
            "approval_id": "visual-approval-20260718-0001",
            "approval_kind": "explicit_user_review",
            "decision": "approved",
            "approved_by": "user:product-owner",
            "approved_at": "2026-07-18T08:00:00Z",
            "source_commit": "1" * 40,
            "demo_report_hash": demo_report_hash or str(_demo_payload()["content_hash"]),
        }
    )


def _validate_json(model, payload: dict[str, object]):
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


def _schema(filename: str) -> dict[str, object]:
    return json.loads((_ROOT / "docs" / "reference" / filename).read_text())


def _schema_errors(filename: str, payload: dict[str, object]) -> list[object]:
    return list(Draft202012Validator(_schema(filename)).iter_errors(payload))


def _step_schema() -> dict[str, object]:
    return _schema("lean-preview-demo-report-v1.schema.json")["$defs"]["LeanPreviewStepResultV1"]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_frozen_models_reject_unknown_fields() -> None:
    module = _module()
    for model, payload in (
        (module.LeanPreviewStepResultV1, _demo_payload()["steps"][0]),
        (module.LeanPreviewDemoReportV1, _demo_payload()),
        (module.LeanPreviewCandidateReportV1, _candidate_payload()),
        (module.LeanPreviewVisualApprovalRecordV1, _approval_payload()),
    ):
        invalid = copy.deepcopy(payload)
        invalid["unexpected"] = True
        with pytest.raises(ValidationError, match="unexpected"):
            _validate_json(model, invalid)


@pytest.mark.parametrize("factory", [_demo_payload, _candidate_payload, _approval_payload])
def test_content_hash_is_the_s3_canonical_hash_with_only_itself_omitted(factory) -> None:
    module = _module()
    payload = factory()
    model = {
        _demo_payload: module.LeanPreviewDemoReportV1,
        _candidate_payload: module.LeanPreviewCandidateReportV1,
        _approval_payload: module.LeanPreviewVisualApprovalRecordV1,
    }[factory]

    report = _validate_json(model, payload)
    unhashed = copy.deepcopy(payload)
    expected = unhashed.pop("content_hash")

    assert module.lean_preview_content_hash(report) == expected
    assert module.lean_preview_content_hash(payload) == expected
    assert canonical_sha256(unhashed) == expected


@pytest.mark.parametrize("factory", [_demo_payload, _candidate_payload, _approval_payload])
def test_noncanonical_or_stale_content_hash_is_rejected(factory) -> None:
    module = _module()
    payload = factory()
    model = {
        _demo_payload: module.LeanPreviewDemoReportV1,
        _candidate_payload: module.LeanPreviewCandidateReportV1,
        _approval_payload: module.LeanPreviewVisualApprovalRecordV1,
    }[factory]
    payload["content_hash"] = _OTHER_DIGEST

    with pytest.raises(ValidationError, match="content hash mismatch"):
        _validate_json(model, payload)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_step",
        "swapped_steps",
        "missing_step",
        "fixture_claims_external_complete",
        "noncanonical_commit_length",
    ],
)
def test_demo_model_and_schema_reject_the_same_structural_corpus(case: str) -> None:
    module = _module()
    payload = _demo_payload()
    steps = payload["steps"]
    if case == "duplicate_step":
        steps[1]["step_id"] = steps[0]["step_id"]
    elif case == "swapped_steps":
        steps[0], steps[1] = steps[1], steps[0]
    elif case == "missing_step":
        steps.pop()
    elif case == "fixture_claims_external_complete":
        payload["fixture"] = True
        payload["external_pending"] = []
    else:
        payload["source_commit"] = "1" * 41
    payload = _rehash(payload)

    with pytest.raises(ValidationError):
        _validate_json(module.LeanPreviewDemoReportV1, payload)
    assert _schema_errors("lean-preview-demo-report-v1.schema.json", payload)


@pytest.mark.parametrize(
    "case",
    [
        "wrong_phase_id",
        "wrong_deferred_id",
        "approved_without_record",
        "approved_without_record_hash",
        "pending_with_record",
        "noncanonical_commit_length",
    ],
)
def test_candidate_model_and_schema_reject_the_same_structural_corpus(case: str) -> None:
    module = _module()
    payload = _candidate_payload()
    if case == "wrong_phase_id":
        payload["phase_report_hashes"]["unexpected_phase"] = payload["phase_report_hashes"].pop(
            _PHASE_REPORT_IDS[-1]
        )
    elif case == "wrong_deferred_id":
        payload["deferred_work_ids"][-1] = "P4-E6"
    elif case == "approved_without_record":
        payload["visual_status"] = "user_approved"
    elif case == "approved_without_record_hash":
        payload["visual_status"] = "user_approved"
        payload["visual_approval_record_ref"] = "visual-approval.json"
    elif case == "pending_with_record":
        payload["visual_approval_record_ref"] = "visual-approval.json"
        payload["visual_approval_record_hash"] = _OTHER_DIGEST
    else:
        payload["source_commit"] = "1" * 63
    payload = _rehash(payload)

    with pytest.raises(ValidationError):
        _validate_json(module.LeanPreviewCandidateReportV1, payload)
    assert _schema_errors("lean-preview-candidate-report-v1.schema.json", payload)


def test_models_and_schemas_accept_valid_pending_and_approved_reports() -> None:
    module = _module()
    demo = _demo_payload()
    pending = _candidate_payload()
    approved = copy.deepcopy(pending)
    approved["visual_status"] = "user_approved"
    approved["visual_approval_record_ref"] = "visual-approval.json"
    approved["visual_approval_record_hash"] = _approval_payload()["content_hash"]
    approved = _rehash(approved)

    assert not _schema_errors("lean-preview-demo-report-v1.schema.json", demo)
    assert not _schema_errors("lean-preview-candidate-report-v1.schema.json", pending)
    assert not _schema_errors("lean-preview-candidate-report-v1.schema.json", approved)
    _validate_json(module.LeanPreviewDemoReportV1, demo)
    _validate_json(module.LeanPreviewCandidateReportV1, pending)
    _validate_json(module.LeanPreviewCandidateReportV1, approved)


@pytest.mark.parametrize(
    ("timestamp", "accepted"),
    [
        ("2026-07-18T00:00:00Z", True),
        ("2026-07-18T08:00:00+08:00", True),
        ("2024-02-29T23:59:59.123456-05:30", True),
        ("0002-01-01T00:00:00+14:00", True),
        ("9998-12-31T23:59:59-14:00", True),
        ("not-a-date", False),
        ("2026-07-18T00:00:00", False),
        ("2026-7-18T00:00:00Z", False),
        ("2026-07-18 00:00:00Z", False),
        ("2026-02-30T00:00:00Z", False),
        ("2025-02-29T00:00:00Z", False),
        ("2026-04-31T00:00:00Z", False),
        ("2026-07-18T24:00:00Z", False),
        ("2026-07-18T00:00:60Z", False),
        ("2026-07-18T00:00:00.1234567Z", False),
        ("2026-07-18T00:00:00+14:01", False),
        ("0000-01-01T00:00:00Z", False),
        ("0001-01-01T00:00:00+14:00", False),
        ("9999-12-31T23:59:59-14:00", False),
        ("2026-07-18T00:00:00Z\n", False),
    ],
)
def test_step_model_and_raw_schema_share_the_aware_rfc3339_contract(
    timestamp: str, accepted: bool
) -> None:
    module = _module()
    payload = copy.deepcopy(_demo_payload()["steps"][0])
    payload["started_at"] = timestamp
    payload["completed_at"] = timestamp

    schema_valid = not list(Draft202012Validator(_step_schema()).iter_errors(payload))
    try:
        _validate_json(module.LeanPreviewStepResultV1, payload)
    except ValidationError:
        model_valid = False
    else:
        model_valid = True

    assert schema_valid is accepted
    assert model_valid is accepted


def test_step_normalizes_offsets_to_utc_and_model_owns_time_order() -> None:
    module = _module()
    payload = copy.deepcopy(_demo_payload()["steps"][0])
    payload["started_at"] = "2026-07-18T08:00:00+08:00"
    payload["completed_at"] = "2026-07-18T08:00:01+08:00"

    step = _validate_json(module.LeanPreviewStepResultV1, payload)

    assert step.started_at == datetime(2026, 7, 18, tzinfo=UTC)
    assert step.completed_at == datetime(2026, 7, 18, 0, 0, 1, tzinfo=UTC)

    payload["completed_at"] = "2026-07-17T23:59:59Z"
    assert not list(Draft202012Validator(_step_schema()).iter_errors(payload))
    with pytest.raises(ValidationError, match="completed_at must not precede started_at"):
        _validate_json(module.LeanPreviewStepResultV1, payload)


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59-14:00",
    ],
)
def test_step_model_converts_unsupported_datetime_boundaries_to_validation_errors(
    timestamp: str,
) -> None:
    module = _module()
    payload = copy.deepcopy(_demo_payload()["steps"][0])
    payload["started_at"] = datetime.fromisoformat(timestamp)
    payload["completed_at"] = datetime.fromisoformat(timestamp)
    payload["evidence_hashes"] = tuple(payload["evidence_hashes"])

    with pytest.raises(ValidationError, match="between 0002 and 9998"):
        module.LeanPreviewStepResultV1.model_validate(payload)


def test_visual_approval_record_uses_the_same_aware_timestamp_contract() -> None:
    module = _module()
    payload = _approval_payload()
    payload["approved_at"] = "2026-07-18 08:00:00"
    payload = _rehash(payload)

    with pytest.raises(ValidationError, match="aware RFC3339"):
        _validate_json(module.LeanPreviewVisualApprovalRecordV1, payload)


def test_candidate_resolver_accepts_bound_non_fixture_demo(tmp_path: Path) -> None:
    module = _module()
    demo_payload = _demo_payload()
    candidate = _validate_json(module.LeanPreviewCandidateReportV1, _candidate_payload())
    _write_json(tmp_path / "demo-report.json", demo_payload)

    resolved = module.resolve_lean_preview_candidate_artifacts(candidate, tmp_path)

    assert resolved.demo_report.content_hash == candidate.demo_report_hash
    assert resolved.visual_approval_record is None


@pytest.mark.parametrize("case", ["fixture", "wrong_hash", "missing", "path_escape"])
def test_candidate_resolver_rejects_unqualified_demo(tmp_path: Path, case: str) -> None:
    module = _module()
    demo = _demo_payload()
    candidate_payload = _candidate_payload()
    if case == "fixture":
        demo["fixture"] = True
        demo = _rehash(demo)
        candidate_payload["demo_report_hash"] = demo["content_hash"]
    elif case == "wrong_hash":
        candidate_payload["demo_report_hash"] = _OTHER_DIGEST
    elif case == "missing":
        candidate_payload["demo_report_ref"] = "missing.json"
    else:
        outside = tmp_path.parent / "outside-demo.json"
        _write_json(outside, demo)
        candidate_payload["demo_report_ref"] = "../outside-demo.json"
    candidate_payload = _rehash(candidate_payload)
    candidate = _validate_json(module.LeanPreviewCandidateReportV1, candidate_payload)
    if case not in {"missing", "path_escape"}:
        _write_json(tmp_path / "demo-report.json", demo)

    with pytest.raises(ValueError, match="demo report"):
        module.resolve_lean_preview_candidate_artifacts(candidate, tmp_path)


def test_candidate_resolver_requires_bound_explicit_user_approval(tmp_path: Path) -> None:
    module = _module()
    demo = _demo_payload()
    approval = _approval_payload(demo_report_hash=str(demo["content_hash"]))
    candidate_payload = _candidate_payload()
    candidate_payload["visual_status"] = "user_approved"
    candidate_payload["visual_approval_record_ref"] = "visual-approval.json"
    candidate_payload["visual_approval_record_hash"] = approval["content_hash"]
    candidate = _validate_json(module.LeanPreviewCandidateReportV1, _rehash(candidate_payload))
    _write_json(tmp_path / "demo-report.json", demo)
    _write_json(tmp_path / "visual-approval.json", approval)

    resolved = module.resolve_lean_preview_candidate_artifacts(candidate, tmp_path)

    assert resolved.visual_approval_record is not None
    assert resolved.visual_approval_record.approval_kind == "explicit_user_review"


@pytest.mark.parametrize("case", ["missing", "wrong_hash", "unapproved", "wrong_demo"])
def test_candidate_resolver_rejects_unbound_or_unapproved_record(tmp_path: Path, case: str) -> None:
    module = _module()
    demo = _demo_payload()
    approval = _approval_payload(demo_report_hash=str(demo["content_hash"]))
    candidate_payload = _candidate_payload()
    candidate_payload["visual_status"] = "user_approved"
    candidate_payload["visual_approval_record_ref"] = "visual-approval.json"
    candidate_payload["visual_approval_record_hash"] = approval["content_hash"]
    if case == "wrong_hash":
        candidate_payload["visual_approval_record_hash"] = _OTHER_DIGEST
    elif case == "unapproved":
        approval["decision"] = "rejected"
        approval = _rehash(approval)
        candidate_payload["visual_approval_record_hash"] = approval["content_hash"]
    elif case == "wrong_demo":
        approval["demo_report_hash"] = _OTHER_DIGEST
        approval = _rehash(approval)
        candidate_payload["visual_approval_record_hash"] = approval["content_hash"]
    candidate = _validate_json(module.LeanPreviewCandidateReportV1, _rehash(candidate_payload))
    _write_json(tmp_path / "demo-report.json", demo)
    if case != "missing":
        _write_json(tmp_path / "visual-approval.json", approval)

    with pytest.raises(ValueError, match="approval record"):
        module.resolve_lean_preview_candidate_artifacts(candidate, tmp_path)


@pytest.mark.parametrize(
    ("filename", "exporter_name"),
    [
        ("lean-preview-demo-report-v1.schema.json", "lean_preview_demo_report_schema"),
        (
            "lean-preview-candidate-report-v1.schema.json",
            "lean_preview_candidate_report_schema",
        ),
    ],
)
def test_checked_in_schemas_are_generated_from_the_behavioral_exporters(
    filename: str, exporter_name: str
) -> None:
    module = _module()
    expected = getattr(module, exporter_name)()

    assert _schema(filename) == expected
    assert expected["additionalProperties"] is False
