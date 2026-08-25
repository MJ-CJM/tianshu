"""Freeze the S3/S4 contracts consumed by the S5 candidate core."""

from __future__ import annotations

import sqlite3

from tianshu.application.evolution_view import EvolutionCenterQueryService
from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.models.decision import DecisionRecordV1, DecisionRequestV1
from tianshu.models.evolution_candidate import EvolutionCandidateV1
from tianshu.models.evolution_view import EvolutionCenterSnapshotV1
from tianshu.models.memorial import Memorial
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_state import RunStateV1
from tianshu.storage.attempt_ledger import AttemptLeaseRepository
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork


def test_s5_consumes_the_s3_and_s4_contracts_without_parallel_envelopes() -> None:
    assert ClosedEvidenceBundleV1.model_config["frozen"] is True
    assert DecisionRequestV1.model_config["frozen"] is True
    assert DecisionRecordV1.model_config["frozen"] is True
    assert RunStateV1.model_config["frozen"] is True
    assert EvolutionCenterSnapshotV1.model_config["frozen"] is True
    assert EvolutionCandidateV1.model_config["frozen"] is True
    assert SqliteUnitOfWork.__name__ == "SqliteUnitOfWork"
    assert Memorial.__name__ == "Memorial"
    assert AttemptLeaseRepository.__name__ == "AttemptLeaseRepository"
    assert OutboxRepository.__name__ == "OutboxRepository"


def test_s5_appends_one_live_migration_with_all_future_owned_tables() -> None:
    previous_tail = MIGRATIONS[-2]
    migration = MIGRATIONS[-1]

    assert migration.version == previous_tail.version + 1
    assert migration.name.startswith(f"{migration.version:04d}_")

    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    assert apply_migrations(connection, MIGRATIONS[:-1]) == tuple(
        item.version for item in MIGRATIONS[:-1]
    )
    assert apply_migrations(connection, MIGRATIONS) == (migration.version,)
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "evolution_candidates",
        "evolution_gate_snapshots",
        "evolution_lifecycle_journal",
        "evolution_promotion_journal",
        "evolution_routing_allocations",
        "run_evolution_assignments",
        "system_snapshots",
        "run_system_bindings",
    }.issubset(tables)
    connection.close()


def test_s4_read_contract_remains_pre_s5_not_enabled() -> None:
    auth = AuthContext(
        principal=Principal(id="s5-test", kind=PrincipalKind.HUMAN, display_name="S5 test"),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="s5-handoff",
    )
    snapshot = EvolutionCenterQueryService().get_snapshot(auth)

    assert snapshot.status == "not_enabled"
    assert snapshot.reason_code == "s5_governed_evolution_not_enabled"
    assert snapshot.candidates == ()
    assert snapshot.routing == ()
