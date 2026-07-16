from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tianshu.evidence.models import (
    CheckEvidenceV1,
    EvidenceVerificationV1,
    ReproductionCommandV1,
)


def test_strict_models_round_trip_and_reject_unknown_fields() -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    check = CheckEvidenceV1(
        check_id="pytest",
        name="pytest",
        status="passed",
        command_fingerprint="a" * 64,
        exit_code=0,
        output_artifact_digest=None,
        started_at=now,
        completed_at=now,
    )

    assert CheckEvidenceV1.model_validate_json(check.model_dump_json()) == check
    with pytest.raises(ValidationError, match="extra"):
        CheckEvidenceV1.model_validate({**check.model_dump(), "stdout": "too much"})
    with pytest.raises(ValidationError, match="frozen"):
        check.status = "failed"


def test_reproduction_command_is_bounded_argv_only_and_secret_free() -> None:
    command = ReproductionCommandV1(
        label="replay through governance",
        argv=("tianshu", "evidence", "replay", "bundle-1"),
        cwd_ref="workspace:main",
        environment_keys=("CI",),
        expected_result_hash=None,
    )
    assert command.argv[0] == "tianshu"

    for argv in (
        ("sh", "-c", "echo sk-abcdefghijklmnopqrstuvwxyz012345"),
        ("x" * 4097,),
        tuple("arg" for _ in range(65)),
    ):
        with pytest.raises(ValidationError):
            ReproductionCommandV1(
                label="unsafe",
                argv=argv,
                cwd_ref="workspace:main",
                environment_keys=(),
                expected_result_hash=None,
            )

    with pytest.raises(ValidationError, match="redacted"):
        ReproductionCommandV1(
            label="unsafe",
            argv=("tianshu", "evidence", "replay"),
            cwd_ref="sk-abcdefghijklmnopqrstuvwxyz012345",
            environment_keys=(),
            expected_result_hash=None,
        )


def test_verification_model_is_strict_and_bounded() -> None:
    verification = EvidenceVerificationV1(
        bundle_id="bundle-1",
        verified=True,
        content_hash="a" * 64,
        artifact_count=1,
        reason_codes=("verified",),
    )
    assert verification.verified
