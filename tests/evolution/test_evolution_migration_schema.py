"""Direct v18 shape, adoption, drift, and immutability contracts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
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
from tianshu.storage.migrations import (
    _EVOLUTION_CANDIDATE_OBJECT_NAMES,
    _EVOLUTION_CANDIDATE_STATEMENTS,
    _LEGACY_ASSIGNMENT_CLEANUP_STATEMENTS,
    _SYSTEM_SNAPSHOTS_OBJECT_NAMES,
    _SYSTEM_SNAPSHOTS_STATEMENTS,
    MIGRATIONS,
)

_V18_VERSION = next(m.version for m in MIGRATIONS if m.name == "0018_governed_evolution_candidates")

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
_V18_INTEGER_COLUMNS = {
    "evolution_candidates": {"schema_version", "gate_snapshot_version", "version"},
    "evolution_gate_snapshots": {"candidate_version", "gate_snapshot_version"},
    "evolution_lifecycle_journal": {"candidate_version"},
    "evolution_promotion_journal": {"candidate_version", "gate_snapshot_version"},
    "evolution_routing_allocations": {
        "routing_version",
        "allocation_basis_points",
        "version",
    },
    "run_evolution_assignments": {"routing_version", "bucket"},
}
_V18_NULLABLE_COLUMNS = {
    "evolution_candidates": {"candidate_id", "routing_json"},
    "evolution_gate_snapshots": {"gate_snapshot_id"},
    "evolution_lifecycle_journal": {
        "journal_id",
        "from_lifecycle",
        "decision_request_id",
    },
    "evolution_promotion_journal": {"promotion_journal_id", "decision_request_id"},
    "evolution_routing_allocations": {"candidate_id"},
    "run_evolution_assignments": {"assignment_id", "candidate_id"},
}
_V18_PRIMARY_KEYS = {
    "evolution_candidates": "candidate_id",
    "evolution_gate_snapshots": "gate_snapshot_id",
    "evolution_lifecycle_journal": "journal_id",
    "evolution_promotion_journal": "promotion_journal_id",
    "evolution_routing_allocations": "candidate_id",
    "run_evolution_assignments": "assignment_id",
}
_V18_DEFAULTS = {("evolution_candidates", "gate_snapshot_version"): "0"}
_V18_UNIQUE_COLUMN_SETS = {
    "evolution_candidates": {
        ("candidate_id",),
        ("kind", "subject_key", "candidate_id"),
    },
    "evolution_gate_snapshots": {
        ("gate_snapshot_id",),
        ("candidate_id", "gate_snapshot_version"),
    },
    "evolution_lifecycle_journal": {
        ("journal_id",),
        ("candidate_id", "candidate_version"),
    },
    "evolution_promotion_journal": {
        ("promotion_journal_id",),
        ("command_key", "status"),
    },
    "evolution_routing_allocations": {("candidate_id",)},
    "run_evolution_assignments": {("assignment_id",), ("memorial_id",)},
}


def test_v31_system_snapshot_objects_are_exact_and_separate_from_v18(
    connection: sqlite3.Connection,
) -> None:
    assert _SYSTEM_SNAPSHOTS_OBJECT_NAMES == (
        "system_snapshots",
        "run_system_bindings",
        "system_snapshots_no_replace",
        "system_snapshots_no_update",
        "system_snapshots_no_delete",
        "run_system_bindings_no_replace",
        "run_system_bindings_no_update",
    )
    assert set(_SYSTEM_SNAPSHOTS_OBJECT_NAMES).isdisjoint(_EVOLUTION_CANDIDATE_OBJECT_NAMES)
    expected = {
        name: " ".join(statement.split())
        for name, statement in zip(
            _SYSTEM_SNAPSHOTS_OBJECT_NAMES,
            _SYSTEM_SNAPSHOTS_STATEMENTS,
            strict=True,
        )
    }
    placeholders = ",".join("?" for _ in _SYSTEM_SNAPSHOTS_OBJECT_NAMES)
    actual = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            f"SELECT name, sql FROM sqlite_master WHERE name IN ({placeholders})",
            _SYSTEM_SNAPSHOTS_OBJECT_NAMES,
        ).fetchall()
    }
    assert actual == expected


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    active = sqlite3.connect(":memory:")
    active.row_factory = sqlite3.Row
    active.execute("PRAGMA foreign_keys=ON")
    apply_migrations(active, MIGRATIONS)
    yield active
    active.close()


def _column_metadata(
    connection: sqlite3.Connection, table: str
) -> tuple[tuple[str, str, int, str | None, int], ...]:
    return tuple(
        (
            str(row["name"]),
            str(row["type"]),
            int(row["notnull"]),
            str(row["dflt_value"]) if row["dflt_value"] is not None else None,
            int(row["pk"]),
        )
        for row in connection.execute(f"PRAGMA table_info({table})")
    )


def _expected_column_metadata(
    table: str,
) -> tuple[tuple[str, str, int, str | None, int], ...]:
    return tuple(
        (
            name,
            "INTEGER" if name in _V18_INTEGER_COLUMNS[table] else "TEXT",
            0 if name in _V18_NULLABLE_COLUMNS[table] else 1,
            _V18_DEFAULTS.get((table, name)),
            1 if name == _V18_PRIMARY_KEYS[table] else 0,
        )
        for name in _V18_TABLE_COLUMNS[table]
    )


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


def _declared_v18_objects() -> dict[str, str]:
    declared = {
        name: " ".join(statement.split())
        for name, statement in zip(
            _EVOLUTION_CANDIDATE_OBJECT_NAMES,
            _EVOLUTION_CANDIDATE_STATEMENTS,
            strict=True,
        )
    }
    # v22 重建了 no_delete 触发器（legacy 占位可清理）；完整重放后的实况以 v22 为准
    declared["run_evolution_assignments_no_delete"] = " ".join(
        _LEGACY_ASSIGNMENT_CLEANUP_STATEMENTS[1].split()
    )
    return declared


def _durable_v18_objects(connection: sqlite3.Connection) -> dict[str, str]:
    placeholders = ",".join("?" for _ in _V18_TABLE_COLUMNS)
    return {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            f"""
            SELECT name, sql FROM sqlite_master
            WHERE tbl_name IN ({placeholders})
              AND type IN ('table', 'index', 'trigger')
              AND sql IS NOT NULL
            """,
            tuple(_V18_TABLE_COLUMNS),
        )
    }


def _v18_data_snapshot(
    connection: sqlite3.Connection,
) -> dict[str, tuple[int, tuple[object, ...], tuple[int, ...], bytes]]:
    snapshots: dict[str, tuple[int, tuple[object, ...], tuple[int, ...], bytes]] = {}
    for table, key in _V18_PRIMARY_KEYS.items():
        columns = _V18_TABLE_COLUMNS[table]
        rows = connection.execute(f"SELECT rowid, * FROM {table} ORDER BY {key}").fetchall()
        payload = {
            "columns": ["rowid", *columns],
            "rows": [list(row) for row in rows],
        }
        snapshots[table] = (
            len(rows),
            tuple(row[key] for row in rows),
            tuple(int(row["rowid"]) for row in rows),
            canonical_json_bytes(payload),
        )
    return snapshots


def _destructively_recreate_declared_v18_shape(connection: sqlite3.Connection) -> None:
    for table in reversed(_V18_TABLE_COLUMNS):
        connection.execute(f"DROP TABLE {table}")
    for statement in _EVOLUTION_CANDIDATE_STATEMENTS:
        connection.execute(statement)


def _assert_v18_data_preserved(
    before: dict[str, tuple[int, tuple[object, ...], tuple[int, ...], bytes]],
    after: dict[str, tuple[int, tuple[object, ...], tuple[int, ...], bytes]],
) -> None:
    assert after == before, "v18 adopt changed sentinel row bytes, counts, rowids, or identities"


def test_v18_locks_complete_table_fk_unique_and_cas_shape(
    connection: sqlite3.Connection,
) -> None:
    assert {table: _column_metadata(connection, table) for table in _V18_TABLE_COLUMNS} == {
        table: _expected_column_metadata(table) for table in _V18_TABLE_COLUMNS
    }
    assert _durable_v18_objects(connection) == _declared_v18_objects()
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
    assert {
        table: _unique_column_sets(connection, table) for table in _V18_TABLE_COLUMNS
    } == _V18_UNIQUE_COLUMN_SETS


def test_v18_exact_shape_is_adopted_without_recreating_objects() -> None:
    # v18 采纳语义的纯净环境：只迁到 v18（完整库的 no_delete 触发器已是 v22 形状，
    # 与 v18 采纳比对必然不符——真实历史库要么无 v18 表、要么是 v18 原始形状）。
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    through_v18 = tuple(m for m in MIGRATIONS if m.version <= _V18_VERSION)
    apply_migrations(connection, through_v18)
    _seed_immutable_rows(connection)
    data_before = _v18_data_snapshot(connection)
    objects_before = _durable_v18_objects(connection)
    connection.execute("DELETE FROM schema_migrations WHERE version = ?", (_V18_VERSION,))
    connection.commit()

    assert apply_migrations(connection, through_v18) == (_V18_VERSION,)
    assert _durable_v18_objects(connection) == objects_before
    _assert_v18_data_preserved(data_before, _v18_data_snapshot(connection))


def test_v18_adopt_sentinel_detects_same_sql_destructive_recreation() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    apply_migrations(connection, tuple(m for m in MIGRATIONS if m.version <= _V18_VERSION))
    _seed_immutable_rows(connection)
    objects_before = _durable_v18_objects(connection)
    data_before = _v18_data_snapshot(connection)

    _destructively_recreate_declared_v18_shape(connection)

    assert _durable_v18_objects(connection) == objects_before
    with pytest.raises(AssertionError, match="v18 adopt changed sentinel"):
        _assert_v18_data_preserved(data_before, _v18_data_snapshot(connection))


@pytest.mark.parametrize("mode", ["partial", "drift"])
def test_v18_partial_or_drifted_shape_is_rejected_and_rolled_back(mode: str) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        if mode == "partial":
            apply_migrations(connection, tuple(m for m in MIGRATIONS if m.version < _V18_VERSION))
            connection.execute("CREATE TABLE evolution_candidates (candidate_id TEXT PRIMARY KEY)")
        else:
            apply_migrations(connection, MIGRATIONS)
            connection.execute("DELETE FROM schema_migrations WHERE version >= ?", (_V18_VERSION,))
            connection.execute("DROP INDEX idx_evolution_candidates_lifecycle")
            connection.execute(
                "CREATE INDEX idx_evolution_candidates_lifecycle ON evolution_candidates(kind)"
            )
        connection.commit()

        with pytest.raises(MigrationExecutionError, match="governed_evolution_candidates"):
            apply_migrations(connection, MIGRATIONS)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (_V18_VERSION,),
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
    decision_payload = {
        "schema_version": 1,
        "candidate_id": candidate.candidate_id,
        "candidate_version": candidate.version,
        "candidate_artifact_digest": candidate.candidate.artifact_digest,
        "gate_snapshot_version": 1,
        "action": "start_canary",
        "risk_tier": "high",
    }
    decision_payload_json = json.dumps(decision_payload, separators=(",", ":"), sort_keys=True)
    connection.execute(
        """
        INSERT INTO decision_requests (
            decision_request_id, schema_version, kind, edict_id, memorial_id,
            request_key, payload_json, payload_hash, requested_by, expires_at,
            status, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "decision-assignment",
            1,
            "governed_apply",
            "edict-assignment",
            "memorial-assignment",
            "candidate-1:start-canary:1",
            decision_payload_json,
            canonical_sha256(decision_payload),
            "principal-reviewer",
            "2026-07-18T09:00:00+00:00",
            "pending",
            1,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        """
        INSERT INTO decision_resolutions (
            decision_request_id, action, reason, payload_json,
            actor_principal_id, actor_display_name, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "decision-assignment",
            "approve",
            "migration sentinel approved",
            '{"schema_version":1}',
            "principal-reviewer",
            "Reviewer",
            NOW.isoformat(),
        ),
    )
    connection.execute(
        """
        UPDATE decision_requests
        SET status = 'resolved', version = 2
        WHERE decision_request_id = 'decision-assignment'
        """
    )
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
            "decision-assignment",
            json.dumps(promotion, separators=(",", ":"), sort_keys=True),
            canonical_sha256(promotion),
            NOW.isoformat(),
        ),
    )
    routing = {
        "allocation_basis_points": 1_000,
        "allocation_seed_id": "seed-v1",
        "routing_version": 1,
    }
    routing_json = json.dumps(routing, separators=(",", ":"), sort_keys=True)
    connection.execute(
        """
        INSERT INTO evolution_routing_allocations (
            candidate_id, routing_version, allocation_basis_points,
            allocation_seed_id, routing_json, routing_hash,
            version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.candidate_id,
            1,
            1_000,
            "seed-v1",
            routing_json,
            canonical_sha256(routing),
            1,
            NOW.isoformat(),
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
        "evolution_routing_allocations": ("candidate_id", candidate.candidate_id),
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
