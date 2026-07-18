from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tianshu.models.canonical import canonical_sha256

_ROOT = Path(__file__).parents[2]
_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64
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


def _models():
    from tianshu.models.lean_preview import (
        LeanPreviewCandidateReportV1,
        LeanPreviewDemoReportV1,
        LeanPreviewStepResultV1,
        lean_preview_content_hash,
    )

    return (
        LeanPreviewStepResultV1,
        LeanPreviewDemoReportV1,
        LeanPreviewCandidateReportV1,
        lean_preview_content_hash,
    )


def _with_content_hash(payload: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(payload)
    result["content_hash"] = canonical_sha256(result)
    return result


def _demo_payload() -> dict[str, object]:
    return _with_content_hash(
        {
            "schema_version": 1,
            "batch_id": "lean-preview-20260718-0001",
            "source_commit": "1" * 40,
            "wheel_sha256": _DIGEST,
            "environment_fingerprint": _OTHER_DIGEST,
            "fixture": False,
            "steps": [
                {
                    "step_id": "doctor_ready",
                    "status": "passed",
                    "started_at": "2026-07-18T00:00:00Z",
                    "completed_at": "2026-07-18T00:00:01Z",
                    "evidence_hashes": [_DIGEST],
                    "observed_state_hash": _OTHER_DIGEST,
                }
            ],
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
    return _with_content_hash(
        {
            "schema_version": 1,
            "source_commit": "1" * 40,
            "phase_report_hashes": dict.fromkeys(_PHASE_REPORT_IDS, _DIGEST),
            "demo_report_hash": _OTHER_DIGEST,
            "wheel_sha256": _DIGEST,
            "sdist_sha256": _OTHER_DIGEST,
            "capability_matrix_hash": _DIGEST,
            "automation_status": "passed",
            "visual_status": "user_approval_pending",
            "visual_approval_record_hash": None,
            "publication_status": "not_authorized",
            "deferred_work_ids": list(_DEFERRED_WORK_IDS),
        }
    )


def _validate_json(model, payload: dict[str, object]):
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


def test_frozen_models_reject_unknown_fields() -> None:
    step_model, demo_model, candidate_model, _ = _models()

    for model, payload in (
        (step_model, _demo_payload()["steps"][0]),
        (demo_model, _demo_payload()),
        (candidate_model, _candidate_payload()),
    ):
        invalid = copy.deepcopy(payload)
        invalid["unexpected"] = True
        with pytest.raises(ValidationError, match="unexpected"):
            _validate_json(model, invalid)


@pytest.mark.parametrize("factory", [_demo_payload, _candidate_payload])
def test_content_hash_is_the_s3_canonical_hash_with_only_itself_omitted(factory) -> None:
    _, demo_model, candidate_model, content_hash = _models()
    payload = factory()
    model = demo_model if factory is _demo_payload else candidate_model

    report = _validate_json(model, payload)
    unhashed = copy.deepcopy(payload)
    expected = unhashed.pop("content_hash")

    assert content_hash(report) == expected
    assert content_hash(payload) == expected
    assert canonical_sha256(unhashed) == expected


@pytest.mark.parametrize("factory", [_demo_payload, _candidate_payload])
def test_noncanonical_or_stale_content_hash_is_rejected(factory) -> None:
    _, demo_model, candidate_model, _ = _models()
    payload = factory()
    model = demo_model if factory is _demo_payload else candidate_model
    payload["content_hash"] = _OTHER_DIGEST if payload["content_hash"] == _DIGEST else _DIGEST

    with pytest.raises(ValidationError, match="content hash mismatch"):
        _validate_json(model, payload)


def test_demo_report_requires_at_least_one_step() -> None:
    _, demo_model, _, _ = _models()
    payload = _demo_payload()
    payload["steps"] = []
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    with pytest.raises(ValidationError, match="steps"):
        _validate_json(demo_model, payload)


def test_demo_report_rejects_duplicate_step_ids() -> None:
    _, demo_model, _, _ = _models()
    payload = _demo_payload()
    duplicate = copy.deepcopy(payload["steps"][0])
    duplicate["status"] = "failed"
    payload["steps"].append(duplicate)
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    with pytest.raises(ValidationError, match="step IDs must be unique"):
        _validate_json(demo_model, payload)


def test_fixture_demo_cannot_be_counted_as_complete_external_evidence() -> None:
    _, demo_model, _, _ = _models()
    payload = _demo_payload()
    payload["fixture"] = True
    payload["external_pending"] = []
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    with pytest.raises(ValidationError, match="fixture demo must keep external evidence pending"):
        _validate_json(demo_model, payload)


def test_candidate_report_requires_every_phase_hash() -> None:
    _, _, candidate_model, _ = _models()
    payload = _candidate_payload()
    del payload["phase_report_hashes"][_PHASE_REPORT_IDS[-1]]
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    with pytest.raises(ValidationError, match="phase report hashes must be complete"):
        _validate_json(candidate_model, payload)


def test_candidate_report_requires_every_deferred_work_id() -> None:
    _, _, candidate_model, _ = _models()
    payload = _candidate_payload()
    payload["deferred_work_ids"] = list(_DEFERRED_WORK_IDS[:-1])
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    with pytest.raises(ValidationError, match="deferred work IDs must be complete"):
        _validate_json(candidate_model, payload)


def test_user_approved_requires_a_separate_approval_record_hash() -> None:
    _, _, candidate_model, _ = _models()
    payload = _candidate_payload()
    payload["visual_status"] = "user_approved"
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    with pytest.raises(ValidationError, match="user approval record"):
        _validate_json(candidate_model, payload)

    payload["visual_approval_record_hash"] = _OTHER_DIGEST
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )
    assert _validate_json(candidate_model, payload).visual_status == "user_approved"


def test_publication_status_cannot_be_upgraded_by_local_evidence() -> None:
    _, _, candidate_model, _ = _models()
    payload = _candidate_payload()
    payload["publication_status"] = "published"
    payload = _with_content_hash(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )

    with pytest.raises(ValidationError, match="not_authorized"):
        _validate_json(candidate_model, payload)


@pytest.mark.parametrize(
    ("filename", "model_index", "schema_id"),
    [
        (
            "lean-preview-demo-report-v1.schema.json",
            1,
            "https://tianshu.dev/schemas/lean-preview-demo-report-v1.schema.json",
        ),
        (
            "lean-preview-candidate-report-v1.schema.json",
            2,
            "https://tianshu.dev/schemas/lean-preview-candidate-report-v1.schema.json",
        ),
    ],
)
def test_checked_in_schemas_are_generated_from_the_frozen_models(
    filename: str, model_index: int, schema_id: str
) -> None:
    models = _models()
    expected = models[model_index].model_json_schema(mode="serialization")
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    expected["$id"] = schema_id

    actual = json.loads((_ROOT / "docs" / "reference" / filename).read_text())

    assert actual == expected
    assert actual["additionalProperties"] is False


def test_demo_json_schema_rejects_missing_or_empty_steps() -> None:
    schema = json.loads(
        (_ROOT / "docs" / "reference" / "lean-preview-demo-report-v1.schema.json").read_text()
    )
    missing = _demo_payload()
    del missing["steps"]
    empty = _demo_payload()
    empty["steps"] = []

    assert list(Draft202012Validator(schema).iter_errors(missing))
    assert list(Draft202012Validator(schema).iter_errors(empty))
