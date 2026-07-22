"""Measured local evidence for deterministic challenger allocation."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.universe.test_challenger_routing import _router, _seed_canary

from tianshu.models.run_assignment import RunAssignmentV1


def test_ten_percent_routes_changed_challenger_overlays_for_stable_run_keys(storage) -> None:
    candidate = _seed_canary(storage, allocation=1_000, seed_id="distribution-v1")
    router = _router(storage, allocation_secret=b"distribution-secret")
    run_keys = tuple(f"distribution-run-{index:05d}" for index in range(10_000))
    created_at = datetime(2026, 7, 18, 10, tzinfo=UTC)

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute(
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            ("distribution-edict", "measure routing", created_at.isoformat()),
        )
        connection.executemany(
            """INSERT INTO memorials (id, edict_id, status, created_at)
               VALUES (?, 'distribution-edict', 'submitted', ?)""",
            ((run_key, created_at.isoformat()) for run_key in run_keys),
        )
        assignments = tuple(
            router.assign_current(
                unit_of_work,
                memorial_id=run_key,
                created_at=created_at,
            )
            for run_key in run_keys
        )
        unit_of_work.commit()

    governed = tuple(item for item in assignments if isinstance(item, RunAssignmentV1))
    challengers = tuple(item for item in governed if item.selected_ref != item.champion_ref)

    assert len(governed) == 10_000
    assert 900 <= len(challengers) <= 1_100
    assert all(item.selected_ref == candidate.candidate for item in challengers)
    assert all(
        router.overlay_for(item.memorial_id).artifact_digest
        == item.selected_ref.artifact_digest
        != item.champion_ref.artifact_digest
        for item in challengers
    )

    restarted = _router(storage, allocation_secret=b"rotated-after-restart")
    assert tuple(restarted.assign(run_key) for run_key in run_keys[:100]) == assignments[:100]
