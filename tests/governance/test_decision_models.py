"""S3 Task 3A strict decision and RunState model contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import ModuleType

import pytest
from pydantic import ValidationError

import tianshu.models.decision as decision_models
import tianshu.models.run_state as run_state_models
from tianshu.models.canonical import canonical_sha256


def _require(module: ModuleType, names: tuple[str, ...]) -> ModuleType:
    missing = [name for name in names if not hasattr(module, name)]
    assert missing == [], f"missing contracts in {module.__name__}: {missing}"
    return module


def _decision_module() -> ModuleType:
    return _require(
        decision_models,
        (
            "DecisionKind",
            "DecisionStatus",
            "DecisionRequestV1",
            "DecisionResolutionV1",
            "RequestDecisionCommand",
            "ResolveDecisionCommand",
            "DecisionRecordV1",
            "validate_resolution_payload",
        ),
    )


def _run_state_module() -> ModuleType:
    return _require(
        run_state_models,
        (
            "RunPhase",
            "PersistedUsageSummaryV1",
            "PersistedChatMessageV1",
            "ToolProposalV1",
            "IterationSummaryV1",
            "AgentContinuationV1",
            "OuterLoopContinuationV1",
            "RunStateV1",
        ),
    )


def _decision_request(**updates: object):
    module = _decision_module()
    now = datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC)
    payload = updates.pop("payload", {"tool_name": "read_file", "arguments": {"path": "x"}})
    values = {
        "decision_request_id": "decision-1",
        "kind": module.DecisionKind.TOOL,
        "edict_id": "edict-1",
        "memorial_id": "memorial-1",
        "request_key": "tool-call:call-1",
        "payload": payload,
        "payload_hash": canonical_sha256(payload),
        "requested_by": "user:operator",
        "expires_at": now + timedelta(minutes=5),
        "status": module.DecisionStatus.PENDING,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    values.update(updates)
    return module.DecisionRequestV1(**values)


def _usage() -> dict[str, object]:
    return {
        "prompt_tokens": 3,
        "completion_tokens": 5,
        "total_tokens": 8,
        "cache_read_tokens": 1,
        "cost_cny": 0.02,
        "actual_model": "demo-model",
        "upstream_provider": None,
    }


def _agent_state(**updates: object):
    module = _run_state_module()
    now = datetime(2026, 7, 15, 2, 3, 4, tzinfo=UTC)
    arguments = updates.pop("arguments", {"path": "README.md"})
    continuation = module.AgentContinuationV1(
        messages=(
            module.PersistedChatMessageV1(
                role="user",
                content="inspect the repository",
                name=None,
                tool_call_id=None,
            ),
        ),
        pending_tool=module.ToolProposalV1(
            tool_call_id="call-1",
            tool_name="read_file",
            arguments=arguments,
            arguments_hash=canonical_sha256(arguments),
            tool_tier="0",
            policy_rule_id="readonly",
            proposed_at=now,
        ),
        iteration=2,
        usage=_usage(),
        checkpoint_ref="artifact:checkpoint-1",
        resolved_decision_id=None,
        side_effect_cursor=0,
    )
    values = {
        "memorial_id": "memorial-1",
        "edict_id": "edict-1",
        "phase": module.RunPhase.WAITING_DECISION,
        "continuation": continuation,
        "checkpoint_ref": "artifact:checkpoint-1",
        "side_effect_cursor": 0,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    values.update(updates)
    return module.RunStateV1(**values)


def test_decision_enums_and_models_are_strict_frozen_and_canonical() -> None:
    module = _decision_module()
    assert {item.value for item in module.DecisionKind} == {
        "tool",
        "outer_loop",
        "plan_review",
        "governed_apply",
    }
    assert {item.value for item in module.DecisionStatus} == {
        "pending",
        "resolved",
        "expired",
        "cancelled",
    }

    request = _decision_request()
    assert request.expires_at.tzinfo is UTC
    assert request.payload_hash == canonical_sha256(request.payload)
    with pytest.raises(ValidationError, match="frozen"):
        request.status = module.DecisionStatus.RESOLVED
    with pytest.raises(ValidationError, match="extra"):
        module.DecisionRequestV1.model_validate(
            {**request.model_dump(mode="python"), "unexpected": True}
        )
    with pytest.raises(ValidationError, match="payload_hash"):
        _decision_request(payload_hash="0" * 64)
    with pytest.raises(ValidationError, match="request_key"):
        _decision_request(request_key="  ")
    with pytest.raises(ValidationError, match="timezone-aware"):
        _decision_request(expires_at=datetime(2026, 7, 15, 1, 2, 3))


def test_decision_timestamps_normalize_to_utc_and_validate_ordering() -> None:
    offset = timezone(timedelta(hours=8))
    created = datetime(2026, 7, 15, 9, 0, tzinfo=offset)
    request = _decision_request(
        created_at=created,
        updated_at=created,
        expires_at=created + timedelta(minutes=5),
    )
    assert request.created_at == datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
    assert request.created_at.tzinfo is UTC
    with pytest.raises(ValidationError, match="updated_at"):
        _decision_request(updated_at=datetime(2026, 7, 15, 0, 0, tzinfo=UTC))
    with pytest.raises(ValidationError, match="expires_at"):
        _decision_request(expires_at=datetime(2026, 7, 15, 1, 0, tzinfo=UTC))


def test_decision_commands_exclude_identity_and_require_reason_and_version() -> None:
    module = _decision_module()
    now = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)
    command = module.RequestDecisionCommand(
        kind=module.DecisionKind.PLAN_REVIEW,
        edict_id="edict-1",
        memorial_id="memorial-1",
        request_key="plan-revision:1",
        payload={"revision": 1},
        expires_at=now,
    )
    assert "requested_by" not in type(command).model_fields
    with pytest.raises(ValidationError, match="requested_by"):
        module.RequestDecisionCommand.model_validate(
            {**command.model_dump(mode="python"), "requested_by": "body-attacker"}
        )
    with pytest.raises(ValidationError, match="reason"):
        module.ResolveDecisionCommand(
            action="approve", reason="\t", payload={"schema_version": 1}, expected_version=1
        )
    with pytest.raises(ValidationError, match="expected_version"):
        module.ResolveDecisionCommand(
            action="approve", reason="valid", payload={"schema_version": 1}, expected_version=0
        )


def test_action_payloads_are_kind_bound_versioned_and_extra_forbidden() -> None:
    module = _decision_module()
    valid = (
        ("tool", "approve", {"schema_version": 1, "grant_scope": "once", "grant_reason": None}),
        ("tool", "reject", {"schema_version": 1}),
        ("tool", "guide", {"schema_version": 1, "guidance": "use the read-only path"}),
        ("outer_loop", "continue", {"schema_version": 1, "feedback": "one more pass"}),
        ("outer_loop", "accept_as_is", {"schema_version": 1}),
        ("outer_loop", "abort", {"schema_version": 1}),
        (
            "outer_loop",
            "modify_acceptance",
            {"schema_version": 1, "acceptance": {"max_outer_iterations": 8}},
        ),
        ("plan_review", "approve", {"schema_version": 1}),
        ("plan_review", "reject", {"schema_version": 1}),
        ("plan_review", "amend", {"schema_version": 1, "amendment": "split step two"}),
        ("governed_apply", "approve", {"schema_version": 1}),
        ("governed_apply", "reject", {"schema_version": 1}),
    )
    for kind, action, payload in valid:
        assert (
            module.validate_resolution_payload(module.DecisionKind(kind), action, payload)
            == payload
        )

    invalid = (
        ("tool", "approve", {"schema_version": 1, "unexpected": True}),
        ("tool", "guide", {"schema_version": 1}),
        ("outer_loop", "amend", {"schema_version": 1, "amendment": "wrong kind"}),
        ("plan_review", "amend", {"schema_version": 2, "amendment": "wrong version"}),
        ("governed_apply", "guide", {"schema_version": 1}),
    )
    for kind, action, payload in invalid:
        with pytest.raises(ValueError):
            module.validate_resolution_payload(module.DecisionKind(kind), action, payload)


def test_resolution_is_strict_frozen_non_blank_and_utc() -> None:
    module = _decision_module()
    resolved_at = datetime(2026, 7, 15, 3, 0, tzinfo=timezone(timedelta(hours=8)))
    resolution = module.DecisionResolutionV1(
        decision_request_id="decision-1",
        action="approve",
        reason="reviewed",
        payload={"schema_version": 1},
        actor_principal_id="user:reviewer",
        actor_display_name="Reviewer",
        resolved_at=resolved_at,
    )
    assert resolution.resolved_at.tzinfo is UTC
    with pytest.raises(ValidationError, match="frozen"):
        resolution.reason = "changed"
    with pytest.raises(ValidationError, match="reason"):
        module.DecisionResolutionV1.model_validate(
            {**resolution.model_dump(mode="python"), "reason": " "}
        )


def test_agent_and_outer_loop_run_state_round_trip_every_field() -> None:
    module = _run_state_module()
    agent = _agent_state()
    assert module.RunStateV1.model_validate_json(agent.model_dump_json()) == agent

    now = agent.created_at
    outer = module.OuterLoopContinuationV1(
        level="L2",
        iteration=3,
        best_output="best-so-far",
        feedback="tighten evidence",
        steer="focus on migration safety",
        history=(
            module.IterationSummaryV1(
                iteration=2,
                level="L1",
                output_artifact_ref="artifact:output-2",
                critic_verdict="fail",
                critic_issue_class="insufficient_evidence",
                feedback="add rollback proof",
                usage=_usage(),
                completed_at=now,
            ),
        ),
        same_issue_streak=1,
        last_critic_issue_class="insufficient_evidence",
        l1_rounds_used=1,
        l2_rounds_used=1,
        consultation_advice="keep the ledger immutable",
        usage=_usage(),
        total_cost_cny=Decimal("1.25"),
        checkpoint_ref="artifact:outer-3",
        resolved_decision_id="decision-outer-2",
        side_effect_cursor=4,
    )
    state = module.RunStateV1(
        memorial_id="memorial-outer",
        edict_id="edict-1",
        phase=module.RunPhase.PAUSED,
        continuation=outer,
        checkpoint_ref="artifact:outer-3",
        side_effect_cursor=4,
        version=7,
        created_at=now,
        updated_at=now + timedelta(seconds=1),
    )
    assert module.RunStateV1.model_validate_json(state.model_dump_json()) == state
    assert state.continuation.kind == "outer_loop"


def test_run_state_models_reject_extra_mutation_negative_counters_and_hash_drift() -> None:
    module = _run_state_module()
    state = _agent_state()
    with pytest.raises(ValidationError, match="frozen"):
        state.version = 2
    with pytest.raises(ValidationError, match="extra"):
        module.RunStateV1.model_validate({**state.model_dump(mode="python"), "stack": "forbidden"})
    with pytest.raises(ValidationError, match="side_effect_cursor"):
        _agent_state(side_effect_cursor=-1)
    with pytest.raises(ValidationError, match="arguments_hash"):
        proposal = state.continuation.pending_tool.model_copy(update={"arguments_hash": "0" * 64})
        module.ToolProposalV1.model_validate(proposal.model_dump(mode="python"))
    with pytest.raises(ValidationError, match="prompt_tokens"):
        module.PersistedUsageSummaryV1(**{**_usage(), "prompt_tokens": -1})
    with pytest.raises(ValidationError, match="unexpected"):
        module.PersistedUsageSummaryV1.model_validate({**_usage(), "unexpected": 1})


def test_run_state_discriminator_and_mirrored_cursor_checkpoint_must_match() -> None:
    module = _run_state_module()
    state = _agent_state()
    raw = state.model_dump(mode="python")
    raw["continuation"] = {**raw["continuation"], "kind": "unknown"}
    with pytest.raises(ValidationError, match="Input tag"):
        module.RunStateV1.model_validate(raw)
    with pytest.raises(ValidationError, match="side_effect_cursor"):
        _agent_state(side_effect_cursor=1)
    with pytest.raises(ValidationError, match="checkpoint_ref"):
        _agent_state(checkpoint_ref="artifact:different")
