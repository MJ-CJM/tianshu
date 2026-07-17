"""Direct v18 shape, adoption, drift, and immutability contracts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateSourceChannel,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
    EvolutionContractV1,
    EvolutionProvenanceV1,
    GateName,
    RollbackSpecV1,
)
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import MIGRATIONS

NOW = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _candidate() -> EvolutionCandidateV1:
    base = CandidateVersionRefV1(
        version="champion-v1", artifact_digest=DIGEST_A, canonical_digest=DIGEST_B
    )
    contract = EvolutionContractV1(
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
        governance_contract_hash=DIGEST_A,
        required_gates=(GateName.SCHEMA, GateName.SECURITY, GateName.EVIDENCE),
        regression_policy_artifact_digest=DIGEST_B,
        sample_policy_artifact_digest=DIGEST_C,
        budget_policy_artifact_digest=DIGEST_D,
        minimum_canary_samples=10,
        max_canary_allocation_basis_points=1_000,
        rollback_slo_seconds=30,
    )
    return EvolutionCandidateV1(
        candidate_id="candidate-1",
        kind=CandidateKind.SKILL,
        subject_key=contract.subject_key,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.REVIEWER,
            source_uri_redacted="review://bundle-1",
            source_digest=DIGEST_C,
            actor_principal_id="principal-1",
            actor_display_name="Reviewer",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="migration-test",
            producer_version="1.0",
            received_at=NOW,
        ),
        base=base,
        candidate=CandidateVersionRefV1(
            version="candidate-v1", artifact_digest=DIGEST_C, canonical_digest=DIGEST_D
        ),
        diff_artifact_digest=DIGEST_D,
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=0,
        evidence_bundle_ids=(),
        routing=None,
        rollback=RollbackSpecV1(
            champion_ref=base,
            restore_point_ref="restore-1",
            adapter_name="skill",
            max_seconds=30,
        ),
        lifecycle=CandidateLifecycle.PROPOSED,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


_V18_TABLE_COLUMNS = {
    "evolution_candidates": (
        "candidate_id",
        "schema_version",
        "kind",
        "subject_key",
        "provenance_json",
        "provenance_hash",
        "base_json",
        "candidate_ref_json",
        "diff_artifact_digest",
        "evolution_contract_json",
        "evolution_contract_hash",
        "gate_snapshot_version",
        "evidence_bundle_ids_json",
        "routing_json",
        "rollback_json",
        "lifecycle",
        "version",
        "created_at",
        "updated_at",
    ),
    "evolution_gate_snapshots": (
        "gate_snapshot_id",
        "candidate_id",
        "candidate_version",
        "gate_snapshot_version",
        "snapshot_json",
        "snapshot_hash",
        "evidence_bundle_ids_json",
        "created_at",
    ),
    "evolution_lifecycle_journal": (
        "journal_id",
        "candidate_id",
        "candidate_version",
        "from_lifecycle",
        "to_lifecycle",
        "decision_request_id",
        "entry_json",
        "entry_hash",
        "created_at",
    ),
    "evolution_promotion_journal": (
        "promotion_journal_id",
        "command_key",
        "candidate_id",
        "candidate_version",
        "gate_snapshot_version",
        "action",
        "status",
        "decision_request_id",
        "entry_json",
        "entry_hash",
        "created_at",
    ),
    "evolution_routing_allocations": (
        "candidate_id",
        "routing_version",
        "allocation_basis_points",
        "allocation_seed_id",
        "routing_json",
        "routing_hash",
        "version",
        "created_at",
        "updated_at",
    ),
    "run_evolution_assignments": (
        "assignment_id",
        "memorial_id",
        "candidate_id",
        "routing_version",
        "bucket",
        "champion_ref_json",
        "selected_ref_json",
        "overlay_digest",
        "assignment_json",
        "assignment_hash",
        "created_at",
    ),
}


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    active = sqlite3.connect(":memory:")
    active.row_factory = sqlite3.Row
    active.execute("PRAGMA foreign_keys=ON")
    apply_migrations(active, MIGRATIONS)
    yield active
    active.close()


def _column_names(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})"))


def _foreign_keys(connection: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    return {
        (str(row["from"]), str(row["table"]), str(row["to"]))
        for row in connection.execute(f"PRAGMA foreign_key_list({table})")
    }


def _unique_column_sets(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    unique: set[tuple[str, ...]] = set()
    for index in connection.execute(f"PRAGMA index_list({table})"):
        if int(index["unique"]) != 1:
            continue
        columns = tuple(
            str(row["name"]) for row in connection.execute(f"PRAGMA index_info({index['name']})")
        )
        unique.add(columns)
    return unique


def test_v18_locks_complete_table_fk_unique_and_cas_shape(
    connection: sqlite3.Connection,
) -> None:
    assert {
        table: _column_names(connection, table) for table in _V18_TABLE_COLUMNS
    } == _V18_TABLE_COLUMNS
    assert _foreign_keys(connection, "evolution_gate_snapshots") == {
        ("candidate_id", "evolution_candidates", "candidate_id")
    }
    assert _foreign_keys(connection, "evolution_lifecycle_journal") == {
        ("candidate_id", "evolution_candidates", "candidate_id"),
        ("decision_request_id", "decision_requests", "decision_request_id"),
    }
    assert _foreign_keys(connection, "evolution_promotion_journal") == {
        ("candidate_id", "evolution_candidates", "candidate_id"),
        ("decision_request_id", "decision_requests", "decision_request_id"),
    }
    assert _foreign_keys(connection, "evolution_routing_allocations") == {
        ("candidate_id", "evolution_candidates", "candidate_id")
    }
    assert _foreign_keys(connection, "run_evolution_assignments") == {
        ("candidate_id", "evolution_candidates", "candidate_id"),
        ("memorial_id", "memorials", "id"),
    }
    assert ("candidate_id", "gate_snapshot_version") in _unique_column_sets(
        connection, "evolution_gate_snapshots"
    )
    assert ("candidate_id", "candidate_version") in _unique_column_sets(
        connection, "evolution_lifecycle_journal"
    )
    assert ("command_key", "status") in _unique_column_sets(
        connection, "evolution_promotion_journal"
    )
    assert ("memorial_id",) in _unique_column_sets(connection, "run_evolution_assignments")
    for table in ("evolution_candidates", "evolution_routing_allocations"):
        version = next(
            row
            for row in connection.execute(f"PRAGMA table_info({table})")
            if row["name"] == "version"
        )
        assert int(version["notnull"]) == 1


def test_v18_exact_shape_is_adopted_without_recreating_objects(
    connection: sqlite3.Connection,
) -> None:
    before = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name LIKE '%evolution%'"
        )
    }
    connection.execute("DELETE FROM schema_migrations WHERE version = ?", (MIGRATIONS[-1].version,))
    connection.commit()

    assert apply_migrations(connection, MIGRATIONS) == (MIGRATIONS[-1].version,)
    after = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name LIKE '%evolution%'"
        )
    }
    assert after == before


@pytest.mark.parametrize("mode", ["partial", "drift"])
def test_v18_partial_or_drifted_shape_is_rejected_and_rolled_back(mode: str) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        if mode == "partial":
            apply_migrations(connection, MIGRATIONS[:-1])
            connection.execute("CREATE TABLE evolution_candidates (candidate_id TEXT PRIMARY KEY)")
        else:
            apply_migrations(connection, MIGRATIONS)
            connection.execute(
                "DELETE FROM schema_migrations WHERE version = ?", (MIGRATIONS[-1].version,)
            )
            connection.execute("DROP INDEX idx_evolution_candidates_lifecycle")
            connection.execute(
                "CREATE INDEX idx_evolution_candidates_lifecycle ON evolution_candidates(kind)"
            )
        connection.commit()

        with pytest.raises(MigrationExecutionError, match="governed_evolution_candidates"):
            apply_migrations(connection, MIGRATIONS)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (MIGRATIONS[-1].version,),
        ).fetchone() == (0,)
        if mode == "partial":
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'evolution_gate_snapshots'"
                ).fetchone()
                is None
            )
        else:
            index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'idx_evolution_candidates_lifecycle'"
            ).fetchone()[0]
            assert index_sql.endswith("evolution_candidates(kind)")
    finally:
        connection.close()


def _seed_immutable_rows(connection: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-assignment", "assignment", NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-assignment", "edict-assignment", "submitted", NOW.isoformat()),
    )
    candidate = EvolutionRepository().insert_candidate(connection, _candidate())
    snapshot = {"schema_version": 1, "candidate_id": candidate.candidate_id, "gates": []}
    connection.execute(
        """
        INSERT INTO evolution_gate_snapshots (
            gate_snapshot_id, candidate_id, candidate_version, gate_snapshot_version,
            snapshot_json, snapshot_hash, evidence_bundle_ids_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gate-snapshot-1",
            candidate.candidate_id,
            candidate.version,
            1,
            json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
            canonical_sha256(snapshot),
            "[]",
            NOW.isoformat(),
        ),
    )
    promotion = {"schema_version": 1, "action": "start_canary"}
    connection.execute(
        """
        INSERT INTO evolution_promotion_journal (
            promotion_journal_id, command_key, candidate_id, candidate_version,
            gate_snapshot_version, action, status, decision_request_id,
            entry_json, entry_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "promotion-1",
            "command-1",
            candidate.candidate_id,
            candidate.version,
            1,
            "start_canary",
            "intended",
            None,
            json.dumps(promotion, separators=(",", ":"), sort_keys=True),
            canonical_sha256(promotion),
            NOW.isoformat(),
        ),
    )
    version_ref = json.dumps(
        candidate.base.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    )
    assignment = {"schema_version": 1, "assignment_id": "assignment-1"}
    connection.execute(
        """
        INSERT INTO run_evolution_assignments (
            assignment_id, memorial_id, candidate_id, routing_version, bucket,
            champion_ref_json, selected_ref_json, overlay_digest,
            assignment_json, assignment_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "assignment-1",
            "memorial-assignment",
            candidate.candidate_id,
            1,
            1,
            version_ref,
            version_ref,
            DIGEST_B,
            json.dumps(assignment, separators=(",", ":"), sort_keys=True),
            canonical_sha256(assignment),
            NOW.isoformat(),
        ),
    )
    lifecycle_id = str(
        connection.execute(
            "SELECT journal_id FROM evolution_lifecycle_journal WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()[0]
    )
    return {
        "evolution_gate_snapshots": ("gate_snapshot_id", "gate-snapshot-1"),
        "evolution_lifecycle_journal": ("journal_id", lifecycle_id),
        "evolution_promotion_journal": ("promotion_journal_id", "promotion-1"),
        "run_evolution_assignments": ("assignment_id", "assignment-1"),
    }


@pytest.mark.parametrize(
    "table",
    [
        "evolution_gate_snapshots",
        "evolution_lifecycle_journal",
        "evolution_promotion_journal",
        "run_evolution_assignments",
    ],
)
def test_v18_snapshot_and_journal_rows_reject_update_and_delete(
    connection: sqlite3.Connection,
    table: str,
) -> None:
    identities = _seed_immutable_rows(connection)
    key, value = identities[table]

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(f"UPDATE {table} SET created_at = created_at WHERE {key} = ?", (value,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(f"DELETE FROM {table} WHERE {key} = ?", (value,))
