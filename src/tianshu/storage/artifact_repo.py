"""Connection-level persistence for immutable artifact and evidence metadata."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from pydantic import ValidationError

from tianshu.evidence.models import (
    ArtifactRefV1,
    ClosedEvidenceBundleV1,
    EvidenceBundleV1,
)
from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.control_center import ControlEvidenceSummaryV1
from tianshu.storage.correlation import correlation_for_memorial
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class ArtifactRepositoryError(RuntimeError):
    """Persisted artifact metadata is missing, conflicting, or corrupt."""


class EvidenceRepositoryError(RuntimeError):
    """Persisted evidence metadata is missing, conflicting, or corrupt."""


class EvidenceConflict(EvidenceRepositoryError):
    """An evidence close lost its optimistic-version race."""


class ArtifactRepository:
    def __init__(self, unit_of_work_factory: Callable[[], SqliteUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    @staticmethod
    def get_current(
        connection: sqlite3.Connection,
        digest: str,
    ) -> ArtifactRefV1 | None:
        row = connection.execute(
            "SELECT * FROM artifact_records WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None:
            return None
        try:
            return ArtifactRefV1(
                digest=row["digest"],
                size_bytes=row["size_bytes"],
                media_type=row["media_type"],
                redaction=row["redaction"],
                uri=row["uri"],
                root_fingerprint=row["root_fingerprint"],
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ArtifactRepositoryError("persisted artifact metadata violates v1") from exc

    @staticmethod
    def total_bytes_current(connection: sqlite3.Connection, root_fingerprint: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM artifact_records WHERE root_fingerprint = ?",
            (root_fingerprint,),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def add_current(
        connection: sqlite3.Connection, artifact: ArtifactRefV1, created_at: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO artifact_records (
                digest, schema_version, size_bytes, media_type, redaction,
                uri, root_fingerprint, created_at
            ) VALUES (?, '1.0', ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.digest,
                artifact.size_bytes,
                artifact.media_type,
                artifact.redaction,
                artifact.uri,
                artifact.root_fingerprint,
                created_at,
            ),
        )

    def get(self, digest: str) -> ArtifactRefV1 | None:
        with self._unit_of_work_factory() as unit_of_work:
            value = self.get_current(unit_of_work.connection, digest)
            unit_of_work.commit()
            return value

    def list_all(self) -> list[ArtifactRefV1]:
        with self._unit_of_work_factory() as unit_of_work:
            rows = unit_of_work.connection.execute(
                "SELECT digest FROM artifact_records ORDER BY digest"
            ).fetchall()
            values = [
                value
                for row in rows
                if (value := self.get_current(unit_of_work.connection, str(row[0]))) is not None
            ]
            unit_of_work.commit()
            return values


class EvidenceRepository:
    def __init__(self, unit_of_work_factory: Callable[[], SqliteUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    @staticmethod
    def _decode(row: sqlite3.Row) -> EvidenceBundleV1 | ClosedEvidenceBundleV1:
        try:
            if row["status"] == "closed":
                return ClosedEvidenceBundleV1.model_validate_json(row["body_json"])
            return EvidenceBundleV1.model_validate_json(row["body_json"])
        except (TypeError, ValueError, ValidationError) as exc:
            raise EvidenceRepositoryError("persisted evidence bundle violates v1") from exc

    @classmethod
    def get_current(
        cls,
        connection: sqlite3.Connection,
        bundle_id: str,
    ) -> EvidenceBundleV1 | ClosedEvidenceBundleV1 | None:
        row = connection.execute(
            "SELECT * FROM evidence_bundles WHERE bundle_id = ?", (bundle_id,)
        ).fetchone()
        return cls._decode(row) if row is not None else None

    @classmethod
    def get_for_memorial_current(
        cls,
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> EvidenceBundleV1 | ClosedEvidenceBundleV1 | None:
        row = connection.execute(
            "SELECT * FROM evidence_bundles WHERE memorial_id = ?", (memorial_id,)
        ).fetchone()
        return cls._decode(row) if row is not None else None

    @staticmethod
    def add_open_current(connection: sqlite3.Connection, bundle: EvidenceBundleV1) -> None:
        correlation_id = correlation_for_memorial(connection, bundle.memorial_id)
        connection.execute(
            """
            INSERT INTO evidence_bundles (
                bundle_id, schema_version, edict_id, memorial_id, status,
                body_json, content_hash, version, created_at, closed_at, correlation_id
            ) VALUES (?, '1.0', ?, ?, 'open', ?, NULL, ?, ?, NULL, ?)
            """,
            (
                bundle.bundle_id,
                bundle.edict_id,
                bundle.memorial_id,
                canonical_json_bytes(bundle).decode("utf-8"),
                bundle.version,
                bundle.created_at.isoformat(),
                correlation_id,
            ),
        )

    @classmethod
    def close_current(
        cls,
        connection: sqlite3.Connection,
        bundle: ClosedEvidenceBundleV1,
        *,
        expected_version: int,
    ) -> ClosedEvidenceBundleV1:
        cursor = connection.execute(
            """
            UPDATE evidence_bundles
            SET status='closed', body_json=?, content_hash=?, version=?, closed_at=?
            WHERE bundle_id=? AND status='open' AND version=?
            """,
            (
                canonical_json_bytes(bundle).decode("utf-8"),
                bundle.content_hash,
                bundle.version,
                bundle.closed_at.isoformat(),
                bundle.bundle_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise EvidenceConflict("evidence close compare-and-swap conflict")
        saved = cls.get_current(connection, bundle.bundle_id)
        if not isinstance(saved, ClosedEvidenceBundleV1):
            raise EvidenceRepositoryError("closed evidence row disappeared")
        return saved

    def get(self, bundle_id: str) -> EvidenceBundleV1 | ClosedEvidenceBundleV1 | None:
        with self._unit_of_work_factory() as unit_of_work:
            value = self.get_current(unit_of_work.connection, bundle_id)
            unit_of_work.commit()
            return value

    def list_for_edict(self, edict_id: str) -> list[EvidenceBundleV1 | ClosedEvidenceBundleV1]:
        with self._unit_of_work_factory() as unit_of_work:
            rows = unit_of_work.connection.execute(
                "SELECT bundle_id FROM evidence_bundles WHERE edict_id=? ORDER BY created_at, bundle_id",
                (edict_id,),
            ).fetchall()
            values = [
                value
                for row in rows
                if (value := self.get_current(unit_of_work.connection, str(row[0]))) is not None
            ]
            unit_of_work.commit()
            return values

    @classmethod
    def list_recent_for_submitter_current(
        cls,
        connection: sqlite3.Connection,
        *,
        submitter: str,
        limit: int,
    ) -> list[ControlEvidenceSummaryV1]:
        if not submitter.strip():
            raise ValueError("submitter must not be blank")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = connection.execute(
            """
            SELECT bundle.*, COALESCE(NULLIF(edict.title, ''), edict.goal) AS edict_title
            FROM evidence_bundles AS bundle
            JOIN edicts AS edict ON edict.id = bundle.edict_id
            WHERE edict.submitter = ?
            ORDER BY COALESCE(bundle.closed_at, bundle.created_at) DESC, bundle.bundle_id
            LIMIT ?
            """,
            (submitter, limit),
        ).fetchall()
        summaries: list[ControlEvidenceSummaryV1] = []
        for row in rows:
            bundle = cls._decode(row)
            summaries.append(
                ControlEvidenceSummaryV1(
                    bundle_id=bundle.bundle_id,
                    edict_id=bundle.edict_id,
                    edict_title=str(row["edict_title"]),
                    memorial_id=bundle.memorial_id,
                    status=bundle.status,
                    content_hash=(
                        bundle.content_hash if isinstance(bundle, ClosedEvidenceBundleV1) else None
                    ),
                    created_at=bundle.created_at,
                    closed_at=(
                        bundle.closed_at if isinstance(bundle, ClosedEvidenceBundleV1) else None
                    ),
                )
            )
        return summaries


__all__ = [
    "ArtifactRepository",
    "ArtifactRepositoryError",
    "EvidenceConflict",
    "EvidenceRepository",
    "EvidenceRepositoryError",
]
