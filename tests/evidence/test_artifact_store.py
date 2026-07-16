from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.evidence.service import (
    ArtifactIntegrityError,
    ArtifactQuotaExceeded,
    ArtifactStore,
)
from tianshu.storage import Storage


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
