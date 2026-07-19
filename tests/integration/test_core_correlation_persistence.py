"""One durable correlation identity crosses core records and restart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.governance_contract import ObjectiveV1, RequestedGovernanceContractV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_state import (
    AgentContinuationV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
)
from tianshu.models.side_effect import SideEffectSemantics, build_side_effect_intent
from tianshu.storage import Storage


def _auth(correlation_id: str) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="correlation-user",
            kind=PrincipalKind.HUMAN,
            display_name="Correlation User",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id=correlation_id,
    )


def test_core_correlation_survives_restart_and_is_queryable(tmp_path) -> None:
    database = tmp_path / "correlation.db"
    now = datetime(2026, 7, 17, 5, 0, tzinfo=UTC)
    root_correlation = "core-correlation-restart"
    storage = Storage(str(database))
    storage.init_db()
    submitted = EdictApplicationService(storage).submit(
        SubmitEdictCommand(
            edict=Edict(goal="preserve correlation"),
            idempotency_key="correlation-chain",
            requested_contract=RequestedGovernanceContractV1(
                objective=ObjectiveV1(goal="preserve correlation")
            ),
            extra_payload={},
        ),
        auth=_auth(root_correlation),
        producer="correlation-test",
        correlation_id=root_correlation,
    )
    decision = DecisionService(storage, clock=lambda: now).request(
        RequestDecisionCommand(
            kind=DecisionKind.TOOL,
            edict_id=submitted.edict.id,
            memorial_id=submitted.memorial.id,
            request_key="correlation-decision",
            payload={"schema_version": 1, "tool_name": "read_file", "arguments": {}},
            expires_at=now + timedelta(minutes=10),
        ),
        auth=_auth("later-http-request-correlation"),
    )
    usage = PersistedUsageSummaryV1(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cache_read_tokens=0,
        cost_cny=0,
        actual_model=None,
        upstream_provider=None,
    )
    continuation = AgentContinuationV1(
        messages=(),
        pending_tool=None,
        iteration=0,
        usage=usage,
        checkpoint_ref=None,
        resolved_decision_id=None,
        side_effect_cursor=0,
    )
    state = RunStateV1(
        memorial_id=submitted.memorial.id,
        edict_id=submitted.edict.id,
        phase=RunPhase.PLANNING,
        continuation=continuation,
        checkpoint_ref=None,
        side_effect_cursor=0,
        version=1,
        created_at=now,
        updated_at=now,
    )
    with storage.unit_of_work() as unit_of_work:
        storage.run_state_repo.create(unit_of_work.connection, state)
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id=submitted.memorial.id,
            available_at=now,
            attempt_id="correlation-attempt",
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id=submitted.memorial.id,
        owner_id="correlation-worker",
        now=now + timedelta(seconds=1),
        lease_seconds=60,
    )
    assert claimed is not None
    intent = build_side_effect_intent(
        effect_id="correlation-effect",
        edict_id=submitted.edict.id,
        memorial_id=submitted.memorial.id,
        attempt_id=claimed.attempt_id,
        owner_id="correlation-worker",
        fencing_token=claimed.fencing_token,
        sequence_no=0,
        boundary="workspace",
        operation="write",
        semantics=SideEffectSemantics.WORKSPACE_ONLY,
        request_metadata={"path_hash": "0" * 64},
        created_at=now + timedelta(seconds=2),
    )
    storage.side_effect_journal.begin_intent(intent)
    assert decision.decision_request_id
    storage.close()

    restarted = Storage(str(database))
    restarted.init_db()
    try:
        assert restarted.get_core_correlation_id(submitted.memorial.id) == root_correlation
        rows = {
            table: restarted._conn.execute(  # noqa: SLF001 - cross-table contract assertion
                f"SELECT correlation_id FROM {table} WHERE memorial_id=? LIMIT 1",
                (submitted.memorial.id,),
            ).fetchone()[0]
            for table in (
                "outbox_events",
                "decision_requests",
                "run_states",
                "execution_attempts",
                "side_effect_journal",
            )
        }
        assert rows == {table: root_correlation for table in rows}
    finally:
        restarted.close()
