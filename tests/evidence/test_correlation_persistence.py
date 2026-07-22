"""Evidence metadata retains the durable ingress correlation across restart."""

from __future__ import annotations

from tianshu.storage import Storage

from ._fixtures import evidence_service, seed_closed_run


def test_evidence_uses_root_correlation_and_survives_restart(tmp_path) -> None:
    database = tmp_path / "evidence-correlation.db"
    root_correlation = "evidence-ingress-correlation"
    storage = Storage(str(database))
    storage.init_db()
    _, memorial = seed_closed_run(storage, correlation_id=root_correlation)
    opened = evidence_service(storage, tmp_path / "artifacts").build_open(memorial.id)
    storage.close()

    restarted = Storage(str(database))
    restarted.init_db()
    try:
        rows = {
            table: restarted._conn.execute(  # noqa: SLF001 - cross-table contract assertion
                f"SELECT correlation_id FROM {table} WHERE memorial_id=? LIMIT 1",
                (memorial.id,),
            ).fetchone()[0]
            for table in ("outbox_events", "run_states", "evidence_bundles")
        }
        assert rows == {table: root_correlation for table in rows}
        assert restarted.get_core_correlation_id(memorial.id) == root_correlation
        assert restarted.evidence_repo.get(opened.bundle_id) is not None
    finally:
        restarted.close()
