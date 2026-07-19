"""Freeze the named S3 contracts consumed by the S4 desktop Web."""

from __future__ import annotations

from dataclasses import fields

import pytest
from pydantic import BaseModel

from tianshu.diagnostics import DoctorCheck, ReadinessReport
from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.models.decision import DecisionRecordV1, DecisionRequestV1
from tianshu.models.run_state import RunStateV1
from tianshu.models.system_audit import SystemAuditEventV1, SystemAuditExportV1


@pytest.mark.parametrize(
    ("model", "properties"),
    [
        (
            DecisionRequestV1,
            {
                "decision_request_id",
                "schema_version",
                "kind",
                "edict_id",
                "memorial_id",
                "request_key",
                "payload",
                "payload_hash",
                "requested_by",
                "expires_at",
                "status",
                "version",
                "created_at",
                "updated_at",
            },
        ),
        (DecisionRecordV1, {"request", "resolution"}),
        (
            RunStateV1,
            {
                "memorial_id",
                "edict_id",
                "schema_version",
                "phase",
                "continuation",
                "checkpoint_ref",
                "side_effect_cursor",
                "version",
                "created_at",
                "updated_at",
            },
        ),
        (
            ClosedEvidenceBundleV1,
            {
                "schema_version",
                "bundle_id",
                "edict_id",
                "memorial_id",
                "status",
                "snapshot",
                "version",
                "created_at",
                "closed_at",
                "content_hash",
            },
        ),
        (
            SystemAuditEventV1,
            {
                "schema_version",
                "id",
                "sequence",
                "correlation_id",
                "actor_digest",
                "action",
                "outcome",
                "reason_code",
                "subject_kind",
                "subject_digest",
                "metadata",
                "previous_hash",
                "event_hash",
                "created_at",
            },
        ),
        (
            SystemAuditExportV1,
            {
                "schema_version",
                "start_sequence",
                "end_sequence",
                "terminal_hash",
                "events",
            },
        ),
    ],
)
def test_s3_named_models_keep_strict_serialization_schemas(
    model: type[BaseModel], properties: set[str]
) -> None:
    schema = model.model_json_schema(mode="serialization")

    assert schema["title"] == model.__name__
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == properties


def test_readiness_report_keeps_summary_and_authenticated_detail_contracts() -> None:
    assert tuple(field.name for field in fields(ReadinessReport)) == (
        "schema_version",
        "status",
        "checks",
    )
    report = ReadinessReport(
        schema_version="1",
        status="ready",
        checks=(
            DoctorCheck(
                id="decision",
                status="pass",
                required=True,
                evidence={"ok": True},
            ),
        ),
    )

    assert report.to_summary_dict() == {"schema_version": "1", "status": "ready"}
    assert report.to_detail_dict() == {
        "schema_version": "1",
        "status": "ready",
        "checks": [
            {
                "id": "decision",
                "status": "pass",
                "required": True,
                "evidence": {"ok": True},
            }
        ],
    }
