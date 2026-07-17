from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from tianshu.evidence.service import (
    ArtifactIntegrityError,
    ArtifactQuotaExceeded,
    ArtifactStore,
)
from tianshu.storage import Storage
from tianshu.storage.unit_of_work import SqliteUnitOfWork


def _publish_shared_artifact(
    database: str,
    root: str,
    data: bytes,
    start: Any,
    attempted: Any,
    done: Any,
    result: Any,
) -> None:
    start.wait()
    attempted.set()
    storage = Storage(database)
    try:
        storage.init_db()
        ArtifactStore(
            Path(root),
            storage.artifact_repo,
            storage.unit_of_work,
            max_object_bytes=1024,
            max_total_bytes=4096,
        ).put_bytes(data, media_type="text/plain", redaction="safe")
    except BaseException as exc:
        result.put(repr(exc))
    else:
        result.put(None)
    finally:
        storage.close()
        done.set()


def _store(
    storage: Storage,
    root: Path,
    *,
    max_object_bytes: int = 1024,
    max_total_bytes: int = 4096,
) -> ArtifactStore:
    return ArtifactStore(
        root,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=max_object_bytes,
        max_total_bytes=max_total_bytes,
    )


def test_content_addressed_store_deduplicates_and_detects_tampering(storage, tmp_path) -> None:
    artifacts = _store(storage, tmp_path / "artifacts")

    first = artifacts.put_bytes(b"canonical evidence", media_type="text/plain", redaction="safe")
    second = artifacts.put_bytes(b"canonical evidence", media_type="text/plain", redaction="safe")

    assert first == second
    assert first.uri == f"artifact://sha256/{first.digest}"
    assert artifacts.get_bytes(first.digest) == b"canonical evidence"
    assert artifacts.verify(first.digest)
    assert len(storage.artifact_repo.list_all()) == 1

    artifact_path = tmp_path / "artifacts" / first.digest[:2] / first.digest
    artifact_path.write_bytes(b"tampered")
    assert not artifacts.verify(first.digest)
    with pytest.raises(ArtifactIntegrityError, match="digest mismatch"):
        artifacts.get_bytes(first.digest)


def test_artifact_store_rejects_traversal_cross_root_secrets_and_size(storage, tmp_path) -> None:
    artifacts = _store(storage, tmp_path / "root-a", max_object_bytes=32, max_total_bytes=32)

    with pytest.raises(ValueError, match="digest"):
        artifacts.get_bytes("../outside")
    with pytest.raises(ValueError, match="secret"):
        artifacts.put_bytes(
            b"token=sk-abcdefghijklmnopqrstuvwxyz012345",
            media_type="text/plain",
            redaction="safe",
        )
    with pytest.raises(ArtifactQuotaExceeded, match="object"):
        artifacts.put_bytes(b"x" * 33, media_type="application/octet-stream", redaction="safe")

    stored = artifacts.put_bytes(b"a" * 24, media_type="application/octet-stream", redaction="safe")
    with pytest.raises(ArtifactQuotaExceeded, match="total"):
        artifacts.put_bytes(b"b" * 16, media_type="application/octet-stream", redaction="safe")

    other_root = _store(storage, tmp_path / "root-b")
    with pytest.raises(ArtifactIntegrityError, match="root"):
        other_root.get_bytes(stored.digest)


def test_failed_commit_removes_only_new_unreferenced_artifact_file(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    artifacts = _store(storage, root)

    def fail_commit(unit_of_work: SqliteUnitOfWork) -> None:
        del unit_of_work
        raise RuntimeError("injected artifact commit failure")

    monkeypatch.setattr(SqliteUnitOfWork, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="injected artifact commit failure"):
        artifacts.put_bytes(b"new artifact", media_type="text/plain", redaction="safe")

    assert storage._conn.execute("SELECT COUNT(*) FROM artifact_records").fetchone()[0] == 0
    assert [path for path in root.rglob("*") if path.is_file()] == []


def test_failed_commit_never_deletes_preexisting_shared_digest(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    artifacts = _store(storage, root)
    shared = artifacts.put_bytes(b"shared artifact", media_type="text/plain", redaction="safe")
    shared_path = root / shared.digest[:2] / shared.digest
    original_commit = SqliteUnitOfWork.commit

    def fail_commit(unit_of_work: SqliteUnitOfWork) -> None:
        del unit_of_work
        raise RuntimeError("injected retry commit failure")

    monkeypatch.setattr(SqliteUnitOfWork, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="injected retry commit failure"):
        artifacts.put_bytes(b"shared artifact", media_type="text/plain", redaction="safe")

    monkeypatch.setattr(SqliteUnitOfWork, "commit", original_commit)
    assert shared_path.read_bytes() == b"shared artifact"
    assert artifacts.get_bytes(shared.digest) == b"shared artifact"


def test_cleanup_serializes_with_independent_process_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "shared.db"
    root = tmp_path / "artifacts"
    storage = Storage(str(database))
    storage.init_db()
    artifacts = _store(storage, root)
    data = b"shared replacement artifact"
    with storage.unit_of_work() as unit_of_work:
        receipt = artifacts.put_bytes_tracked_current(
            unit_of_work.connection,
            data,
            media_type="text/plain",
            redaction="safe",
        )
        unit_of_work.rollback()

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    attempted = context.Event()
    done = context.Event()
    result = context.Queue()
    process = context.Process(
        target=_publish_shared_artifact,
        args=(str(database), str(root), data, start, attempted, done, result),
    )
    original_read = artifacts._read_verified

    def open_cleanup_window(artifact: object) -> bytes:
        verified = original_read(artifact)  # type: ignore[arg-type]
        start.set()
        assert attempted.wait(5)
        assert not done.wait(2)
        return verified

    monkeypatch.setattr(artifacts, "_read_verified", open_cleanup_window)
    process.start()
    artifacts.discard_uncommitted([receipt])
    process.join(10)

    assert process.exitcode == 0
    assert done.is_set()
    assert result.get(timeout=1) is None
    path = root / receipt.artifact.digest[:2] / receipt.artifact.digest
    assert path.read_bytes() == data
    storage.close()


def test_cleanup_rechecks_metadata_after_stale_initial_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "shared.db"
    root = tmp_path / "artifacts"
    first_storage = Storage(str(database))
    first_storage.init_db()
    first = _store(first_storage, root)
    data = b"metadata race artifact"
    with first_storage.unit_of_work() as unit_of_work:
        receipt = first.put_bytes_tracked_current(
            unit_of_work.connection,
            data,
            media_type="text/plain",
            redaction="safe",
        )
        unit_of_work.rollback()

    second_storage = Storage(str(database))
    second_storage.init_db()
    second = _store(second_storage, root)
    original_has_metadata = first._has_durable_metadata

    def commit_after_initial_decision(digest: str) -> bool:
        assert original_has_metadata(digest) is False
        second.put_bytes(data, media_type="text/plain", redaction="safe")
        return False

    monkeypatch.setattr(first, "_has_durable_metadata", commit_after_initial_decision)
    try:
        first.discard_uncommitted([receipt])
        row = first_storage._conn.execute(
            "SELECT COUNT(*) FROM artifact_records WHERE digest = ?",
            (receipt.artifact.digest,),
        ).fetchone()
        assert row is not None and row[0] == 1
        path = root / receipt.artifact.digest[:2] / receipt.artifact.digest
        assert path.read_bytes() == data
    finally:
        first_storage.close()
        second_storage.close()
