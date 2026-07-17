from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.evidence.service import (
    ArtifactIntegrityError,
    ArtifactQuotaExceeded,
    ArtifactStore,
)
from tianshu.storage import Storage
from tianshu.storage.unit_of_work import SqliteUnitOfWork


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
