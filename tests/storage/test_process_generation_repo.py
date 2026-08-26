"""Process-scope release storage stays isolated from executor bindings."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.runtime_generation import (
    PROCESS_GENERATION_SCOPE,
    RuntimeGenerationState,
    RuntimeGenerationV1,
)
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.generation_repo import (
    GenerationRepository,
    GenerationRepositoryConflict,
)
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository

_NOW = datetime(2026, 8, 27, tzinfo=UTC)
_GENERATION_ID = "rg-" + "a" * 32


def _snapshot(marker: str = "a") -> SystemSnapshotV1:
    components = {"kernel": marker * 64}
    return SystemSnapshotV1(
        components=components,
        digest=canonical_sha256(components),
    )


def test_process_release_is_canonical_narrow_and_never_attempt_bindable(storage) -> None:
    repository = GenerationRepository()
    snapshot = _snapshot()
    generation = RuntimeGenerationV1(
        generation_id=_GENERATION_ID,
        scope=PROCESS_GENERATION_SCOPE,
        release_digest=snapshot.digest,
        state=RuntimeGenerationState.STAGED,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        assert (
            repository.insert_process_release(
                connection,
                snapshot,
                first_seen_at=_NOW,
            )
            == snapshot
        )
        assert (
            repository.insert_process_release(
                connection,
                snapshot,
                first_seen_at=_NOW,
            )
            == snapshot
        )
        assert (
            repository.get_process_release(
                connection,
                release_digest=snapshot.digest,
            )
            == snapshot
        )
        durable = repository.insert_staged(connection, generation)
        row = connection.execute(
            """
            SELECT release_digest, scope, release_json
            FROM runtime_generation_releases
            WHERE release_digest = ?
            """,
            (snapshot.digest,),
        ).fetchone()

        assert durable == generation
        assert row is not None
        assert row["release_digest"] == snapshot.digest
        assert row["scope"] == PROCESS_GENERATION_SCOPE
        assert row["release_json"] == canonical_json_bytes(snapshot).decode("utf-8")
        assert repository.list_recovery_candidates(
            connection,
            scope=PROCESS_GENERATION_SCOPE,
        ) == (generation,)
        assert (
            repository.list_recovery_candidates(
                connection,
                scope="executor:keqing:pi",
            )
            == ()
        )
        with pytest.raises(ValueError, match="get_process_release"):
            repository.get_release(
                connection,
                scope=PROCESS_GENERATION_SCOPE,
                release_digest=snapshot.digest,
            )
        with pytest.raises(GenerationRepositoryConflict, match="cannot be bound"):
            repository.validate_generation_ids(connection, (_GENERATION_ID,))
        unit_of_work.commit()


def test_system_snapshot_repository_exposes_read_only_digest_lookup(storage) -> None:
    repository = SystemSnapshotRepository()
    snapshot = _snapshot("b")

    with storage.unit_of_work() as unit_of_work:
        repository.insert_snapshot(unit_of_work.connection, snapshot)
        assert repository.get_snapshot(unit_of_work.connection, snapshot.digest) == snapshot
        assert repository.get_snapshot(unit_of_work.connection, "f" * 64) is None
        unit_of_work.commit()
