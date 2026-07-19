"""Pure reconstruction of fresh outer-loop runtime state from durable evidence."""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.executor.orchestrator.human_decision import HumanDecision
from tianshu.executor.orchestrator.state import (
    ChecksResult,
    CriticResult,
    IterationRecord,
    OuterLoopState,
)
from tianshu.models.common import UsageSummary
from tianshu.models.decision import DecisionKind, DecisionStatus
from tianshu.models.run_state import OuterLoopContinuationV1, RunPhase
from tianshu.storage import Storage


@dataclass(frozen=True, slots=True)
class OuterLoopResume:
    state: OuterLoopState
    decision: HumanDecision


def load_outer_loop_resume(
    storage: Storage,
    memorial_id: str,
    edict_id: str,
) -> OuterLoopResume | None:
    """Load and validate one resolved outer-loop continuation without live objects."""

    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial_id)
        if (
            state is None
            or state.edict_id != edict_id
            or state.phase is not RunPhase.EXECUTING
            or not isinstance(state.continuation, OuterLoopContinuationV1)
            or state.continuation.pending_decision_id is not None
            or state.continuation.resolved_decision_id is None
        ):
            unit_of_work.commit()
            return None
        decision_id = state.continuation.resolved_decision_id
        record = storage.decision_repo.get(unit_of_work.connection, decision_id)
        if (
            record is None
            or record.request.kind is not DecisionKind.OUTER_LOOP
            or record.request.status is not DecisionStatus.RESOLVED
            or record.resolution is None
            or record.request.edict_id != edict_id
            or record.request.memorial_id != memorial_id
        ):
            unit_of_work.commit()
            return None
        continuation = state.continuation
        resolution = record.resolution
        unit_of_work.commit()
    payload = resolution.payload
    decision = HumanDecision(
        action=resolution.action,
        feedback=(payload.get("feedback") if isinstance(payload.get("feedback"), str) else None),
        new_acceptance=(
            payload.get("acceptance") if resolution.action == "modify_acceptance" else None
        ),
    )
    return OuterLoopResume(state=_reconstruct(continuation, edict_id), decision=decision)


def reconstruct_outer_loop(
    storage: Storage,
    memorial_id: str,
    edict_id: str,
) -> OuterLoopState | None:
    """Return only the reconstructed state for diagnostics and focused consumers."""

    resumed = load_outer_loop_resume(storage, memorial_id, edict_id)
    return resumed.state if resumed is not None else None


def _reconstruct(continuation: OuterLoopContinuationV1, edict_id: str) -> OuterLoopState:
    history: list[IterationRecord] = []
    last_index = len(continuation.history) - 1
    for index, summary in enumerate(continuation.history):
        verdict = summary.critic_verdict
        critic = None
        if verdict in {"pass", "fail"}:
            critic = CriticResult(
                verdict=verdict,
                issue_class=summary.critic_issue_class,
                feedback=summary.feedback or "",
                cost_cny=summary.usage.cost_cny,
                usage=UsageSummary.model_validate(summary.usage.model_dump(mode="python")),
            )
        actor_output = summary.output_artifact_ref or ""
        if index == last_index and continuation.best_output is not None:
            actor_output = continuation.best_output
        history.append(
            IterationRecord(
                iteration=summary.iteration,
                level=summary.level,
                actor_output=actor_output,
                checks_result=ChecksResult(all_passed=verdict == "pass"),
                critic_result=critic,
                started_at=summary.completed_at,
                finished_at=summary.completed_at,
                cost_cny=summary.usage.cost_cny,
            )
        )
    return OuterLoopState(
        edict_id=edict_id,
        iteration=continuation.iteration,
        current_level=continuation.level,
        same_issue_streak=continuation.same_issue_streak,
        last_critic_issue_class=continuation.last_critic_issue_class,
        l1_rounds_used=continuation.l1_rounds_used,
        l2_rounds_used=continuation.l2_rounds_used,
        consultation_advice=continuation.consultation_advice,
        steer_note=continuation.steer,
        history=tuple(history),
        total_cost_cny=float(continuation.total_cost_cny),
    )


__all__ = ["OuterLoopResume", "load_outer_loop_resume", "reconstruct_outer_loop"]
