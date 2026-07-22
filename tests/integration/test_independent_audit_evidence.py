from __future__ import annotations

import pytest

from tests.evidence._fixtures import NOW, evidence_service, seed_closed_run
from tianshu.evidence.service import EvidenceIncompleteError
from tianshu.governance.decision_service import DecisionService
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.governance_contract import AcceptanceCheckV1, AcceptancePolicyV1
from tianshu.models.principal import AuthContext, Principal


@pytest.mark.parametrize(
    ("seed_kwargs", "missing"),
    [
        (
            {
                "acceptance": AcceptancePolicyV1(
                    checks=(
                        AcceptanceCheckV1(
                            kind="bash",
                            name="pytest",
                            command="pytest -q",
                        ),
                    )
                )
            },
            "check:pytest",
        ),
        ({"side_effect_cursor": 1}, "effect:cursor:0"),
    ],
)
def test_independent_audit_refuses_missing_required_check_or_effect(
    storage, tmp_path, seed_kwargs, missing
) -> None:
    _, memorial = seed_closed_run(storage, **seed_kwargs)
    service = evidence_service(storage, tmp_path / "artifacts")
    opened = service.build_open(memorial.id)

    with pytest.raises(EvidenceIncompleteError) as error:
        service.close(memorial.id, expected_version=opened.version)

    assert missing in error.value.missing_evidence


def test_independent_audit_refuses_missing_required_decision(storage, tmp_path) -> None:
    _, memorial = seed_closed_run(storage)
    DecisionService(storage, clock=lambda: NOW).request(
        RequestDecisionCommand(
            kind=DecisionKind.PLAN_REVIEW,
            edict_id=memorial.edict_id,
            memorial_id=memorial.id,
            request_key="plan-review:evidence",
            payload={"revision": 1},
            expires_at=NOW.replace(hour=9),
        ),
        auth=AuthContext(
            principal=Principal(
                id="user:evidence-reviewer",
                kind="human",
                display_name="Evidence reviewer",
                scopes=frozenset({"api"}),
            ),
            source="bearer",
            client_kind="api",
            correlation_id="evidence-review",
        ),
    )
    service = evidence_service(storage, tmp_path / "artifacts")
    opened = service.build_open(memorial.id)

    with pytest.raises(EvidenceIncompleteError) as error:
        service.close(memorial.id, expected_version=opened.version)

    assert any(item.startswith("decision:") for item in error.value.missing_evidence)
