"""Migration v3 freezes requested contracts and stores effective contracts per run."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tianshu.executor.adapters import DelegatingExecutorAdapter, ExecutorAdapterRegistry
from tianshu.executor.capabilities import (
    CapabilityDeclarationV1,
    HostCapabilityProbeV1,
    native_manifest,
    resolve_governance_contract,
)
from tianshu.models import Edict, Memorial
from tianshu.models.edict import EdictRuntime
from tianshu.models.governance_contract import (
    EffectiveGovernanceContractV1,
    LegacyEdictGovernanceMapper,
    RequestedGovernanceContractV1,
)
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS

_V1_CHECKSUM = "9672603c12dd858ea714b291d6ed94f1a27cb373bfcff97665b6316b4aa552a6"
_V2_CHECKSUM = "a2bbf753e0c3244fccc86be2d4588af2c926399f6dfa0dba0af5d0c060179c5a"
_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "governance"


def _probe(probe_id: str, network_state: str = "best_effort") -> HostCapabilityProbeV1:
    return HostCapabilityProbeV1(
        probe_id=probe_id,
        os_name="test-os",
        architecture="test-arch",
        git_available=True,
        process_groups_available=True,
        sandbox_backend=None,
        overrides=(
            CapabilityDeclarationV1(
                capability="network_control",
                state=network_state,
                evidence=(probe_id,),
            ),
        ),
    )


def test_migration_v3_is_frozen_and_keeps_v1_v2_checksums() -> None:
    assert [(migration.version, migration.name) for migration in MIGRATIONS[:3]] == [
        (1, "0001_adopt_v042_baseline"),
        (2, "0002_auth_tokens"),
        (3, "0003_governance_contracts"),
    ]
    assert MIGRATIONS[0].checksum == _V1_CHECKSUM
    assert MIGRATIONS[1].checksum == _V2_CHECKSUM


def test_explicit_requested_contract_roundtrips_with_hash(storage) -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="frozen", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    )
    edict = Edict(goal="frozen", governance_contract=contract)

    storage.save_edict(edict)

    loaded = storage.get_edict(edict.id)
    assert loaded is not None
    assert loaded.governance_contract == contract
    record = storage.get_requested_governance_contract_record(edict.id)
    assert record is not None
    assert record["contract_hash"] == contract.content_hash
    assert record["source"] == "explicit"


def test_legacy_edict_is_derived_once_and_persisted(storage) -> None:
    edict = Edict(
        goal="legacy",
        runtime=EdictRuntime(executor="keqing:codex", token_budget=99),
    )

    storage.save_edict(edict)

    first = storage.get_edict(edict.id)
    second = storage.get_edict(edict.id)
    assert first is not None and first.governance_contract is not None
    assert second is not None and second.governance_contract is not None
    assert first.governance_contract.content_hash == second.governance_contract.content_hash
    assert first.governance_contract.executor.adapter_id == "keqing:codex"
    assert storage.get_requested_governance_contract_record(edict.id)["source"] == "legacy_derived"


def test_effective_contract_is_stored_per_memorial_not_globally(storage) -> None:
    edict = Edict(goal="two runs")
    storage.save_edict(edict)
    contract = storage.get_edict(edict.id).governance_contract
    assert contract is not None
    first = Memorial(edict_id=edict.id)
    second = Memorial(edict_id=edict.id)
    storage.save_memorial(first)
    storage.save_memorial(second)
    effective_one = resolve_governance_contract(
        contract,
        native_manifest(),
        _probe("probe-one", "best_effort"),
    )
    effective_two = resolve_governance_contract(
        contract,
        native_manifest(),
        _probe("probe-two", "unsupported"),
    )

    storage.save_effective_governance_contract(first.id, edict.id, effective_one)
    storage.save_effective_governance_contract(second.id, edict.id, effective_two)

    loaded_first = storage.get_memorial(first.id)
    loaded_second = storage.get_memorial(second.id)
    assert (
        loaded_first.effective_governance_contract.runtime_probe_id
        == _probe("probe-one", "best_effort").semantic_id
    )
    assert (
        loaded_second.effective_governance_contract.runtime_probe_id
        == _probe("probe-two", "unsupported").semantic_id
    )
    assert loaded_first.effective_governance_contract.content_hash != (
        loaded_second.effective_governance_contract.content_hash
    )
    assert storage.get_edict(edict.id).governance_contract == contract


def test_effective_v1_from_2e76851_keeps_its_canonical_json_and_hash(storage) -> None:
    raw = (_FIXTURE_DIR / "effective_v1_2e76851.json").read_text().strip()
    expected_hash = (_FIXTURE_DIR / "effective_v1_2e76851.sha256").read_text().strip()
    legacy_effective = EffectiveGovernanceContractV1.model_validate_json(raw)

    assert legacy_effective.canonical_json() == raw
    assert legacy_effective.content_hash == expected_hash

    requested = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="legacy effective fixture"),
        default_workspace_id="workspace-main",
    )
    edict = Edict(goal="legacy effective fixture", governance_contract=requested)
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id)
    storage.save_memorial(memorial)
    data = json.loads(raw)
    with storage._lock, storage._conn:  # noqa: SLF001 - compatibility fixture insertion
        storage._conn.execute(  # noqa: SLF001
            """
            INSERT INTO effective_governance_contracts
                (memorial_id, edict_id, schema_version, requested_contract_hash,
                 contract_json, contract_hash, executor_manifest_id,
                 executor_manifest_version, executor_manifest_hash, runtime_probe_id,
                 created_at)
            VALUES (?, ?, '1', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memorial.id,
                edict.id,
                data["requested_contract_hash"],
                raw,
                expected_hash,
                data["executor_manifest_id"],
                data["executor_manifest_version"],
                data["executor_manifest_hash"],
                data["runtime_probe_id"],
                "2026-07-11T00:00:00+00:00",
            ),
        )

    assert storage.get_memorial(memorial.id).effective_governance_contract is not None
    assert storage.list_memorials_by_edict(edict.id)[0].effective_governance_contract is not None

    legacy_probe = HostCapabilityProbeV1(
        probe_id="probe-legacy",
        os_name="test-os",
        architecture="test-arch",
        git_available=True,
        process_groups_available=True,
        sandbox_backend=None,
    )
    registry = ExecutorAdapterRegistry(
        (
            DelegatingExecutorAdapter(
                adapter_id="native",
                manifest=native_manifest(),
                delegate=object(),
                probe_factory=lambda: legacy_probe,
            ),
        )
    )
    # This frozen N-1 contract now fails at the earlier manifest-hash guard;
    # probe semantic drift remains covered by the adapter compatibility suite.
    with pytest.raises(ValueError, match="manifest drift"):
        registry.bind_effective(
            legacy_effective,
            run_id=memorial.id,
            instruction="legacy effective fixture",
            execution_mode="single",
        )


def test_v3_backfills_legacy_rows_created_at_v2() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn, MIGRATIONS[:2])
    conn.execute(
        """
        INSERT INTO edicts (id, title, goal, context, created_at, runtime_json)
        VALUES ('legacy-edict', 'Legacy', 'backfill me', NULL,
                '2026-07-11T00:00:00+00:00',
                '{"executor":"keqing:claude-code","timeout_seconds":90}')
        """
    )
    conn.commit()

    assert apply_migrations(conn, MIGRATIONS[:3]) == (3,)

    row = conn.execute(
        """
        SELECT schema_version, contract_hash, source
        FROM requested_governance_contracts
        WHERE edict_id='legacy-edict'
        """
    ).fetchone()
    assert row["schema_version"] == "1"
    assert len(row["contract_hash"]) == 64
    assert row["source"] == "legacy_derived"
    assert (
        conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()[
            0
        ]["checksum"]
        == _V1_CHECKSUM
    )
    conn.close()


def test_v3_backfills_legacy_zero_runtime_limits() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn, MIGRATIONS[:2])
    conn.execute(
        """
        INSERT INTO edicts (id, title, goal, context, created_at, runtime_json)
        VALUES ('legacy-zero-limits', 'Legacy', 'backfill zero limits', NULL,
                '2026-07-11T00:00:00+00:00',
                '{"timeout_seconds":0,"max_iterations":0,"max_concurrency":0,"retry_limit":-1,"token_budget":0,"cost_budget_cny":0}')
        """
    )
    conn.commit()

    assert apply_migrations(conn, MIGRATIONS[:3]) == (3,)

    row = conn.execute(
        """
        SELECT contract_json
        FROM requested_governance_contracts
        WHERE edict_id='legacy-zero-limits'
        """
    ).fetchone()
    contract = RequestedGovernanceContractV1.model_validate_json(row["contract_json"])
    assert contract.budget.wall_clock_seconds == 1
    assert contract.budget.max_iterations == 1
    assert contract.budget.max_concurrency == 1
    assert contract.budget.retry_limit == 0
    assert contract.budget.token_limit is None
    assert contract.budget.cost_limit_cny is None
    conn.close()
