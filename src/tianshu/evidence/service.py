"""Content-addressed artifact and immutable Evidence Bundle services."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import ValidationError

from tianshu.evidence.models import (
    ArtifactRefV1,
    AuditorConclusionV1,
    CheckEvidenceV1,
    ClosedEvidenceBundleV1,
    CostEvidenceV1,
    DecisionEvidenceV1,
    EffectEvidenceV1,
    EnvironmentEvidenceV1,
    EvidenceBundleV1,
    EvidenceRequirementsV1,
    EvidenceSnapshotV1,
    EvidenceVerificationV1,
    ExecutorManifestEvidenceV1,
    ReproductionCommandV1,
    closed_bundle_content_hash,
)
from tianshu.executor.capabilities import get_executor_manifest
from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.governance_contract import (
    EffectiveGovernanceContractV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.run_state import RunPhase, agent_plan_continuation
from tianshu.security.redact import redact_text
from tianshu.security.sensitive_payload import contains_raw_sensitive_payload
from tianshu.storage.artifact_repo import (
    ArtifactRepository,
    ArtifactRepositoryError,
    EvidenceConflict,
    EvidenceRepository,
    EvidenceRepositoryError,
)
from tianshu.storage.run_state_repo import RunStateRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStoreError(RuntimeError):
    """Base error for bounded content-addressed storage."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Artifact metadata, root binding, path, size, or digest is invalid."""


class ArtifactQuotaExceeded(ArtifactStoreError):
    """The per-object or total artifact quota would be exceeded."""


@dataclass(frozen=True)
class ArtifactWriteReceipt:
    """Tracks a file created by one uncommitted metadata transaction."""

    artifact: ArtifactRefV1
    created_file_identity: tuple[int, int] | None


class EvidenceServiceError(RuntimeError):
    """Base error for evidence construction, closure, and verification."""


class EvidenceNotFound(EvidenceServiceError):
    """The requested run or evidence bundle does not exist."""


class EvidenceIncompleteError(EvidenceServiceError):
    """A run cannot close because mandatory independent evidence is absent."""

    def __init__(self, missing_evidence: tuple[str, ...]) -> None:
        self.missing_evidence = tuple(sorted(set(missing_evidence)))
        super().__init__("evidence is incomplete: " + ", ".join(self.missing_evidence))


class EvidenceImportError(EvidenceServiceError):
    """An exported bundle failed bounded independent verification."""


class _EvidenceStorage(Protocol):
    artifact_repo: ArtifactRepository
    evidence_repo: EvidenceRepository
    run_state_repo: RunStateRepository

    def unit_of_work(self) -> SqliteUnitOfWork: ...


class ArtifactStore:
    def __init__(
        self,
        root: str | Path,
        repository: ArtifactRepository,
        unit_of_work_factory: Callable[[], SqliteUnitOfWork],
        *,
        max_object_bytes: int,
        max_total_bytes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(max_object_bytes) is not int or max_object_bytes <= 0:
            raise ValueError("max_object_bytes must be a positive integer")
        if type(max_total_bytes) is not int or max_total_bytes < max_object_bytes:
            raise ValueError("max_total_bytes must be at least max_object_bytes")
        expanded = Path(root).expanduser()
        expanded.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root = expanded.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("artifact root must be a directory")
        os.chmod(self._root, 0o700)
        self._root_fingerprint = hashlib.sha256(os.fsencode(self._root)).hexdigest()
        self._lock_path = self._root.parent / f".tianshu-artifact-{self._root_fingerprint}.lock"
        self._repository = repository
        self._unit_of_work_factory = unit_of_work_factory
        self._max_object_bytes = max_object_bytes
        self._max_total_bytes = max_total_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def root_fingerprint(self) -> str:
        return self._root_fingerprint

    @property
    def is_ready(self) -> bool:
        """Whether the configured artifact root remains an accessible directory."""
        return self._root.is_dir() and os.access(self._root, os.R_OK | os.W_OK)

    def _path(self, digest: str, *, create_parent: bool = False) -> Path:
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError("artifact digest must be 64 lowercase hexadecimal characters")
        parent = self._root / digest[:2]
        if create_parent:
            parent.mkdir(mode=0o700, exist_ok=True)
        resolved_parent = parent.resolve(strict=create_parent)
        if resolved_parent.parent != self._root:
            raise ArtifactIntegrityError("artifact path escapes configured root")
        return resolved_parent / digest

    @staticmethod
    def _reject_secret(data: bytes) -> None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return
        if contains_raw_sensitive_payload(text):
            raise ValueError("raw secret is not allowed in artifact bytes")

    def put_bytes(self, data: bytes, *, media_type: str, redaction: str) -> ArtifactRefV1:
        writes: list[ArtifactWriteReceipt] = []
        try:
            with self._unit_of_work_factory() as unit_of_work:
                write = self.put_bytes_tracked_current(
                    unit_of_work.connection,
                    data,
                    media_type=media_type,
                    redaction=redaction,
                )
                writes.append(write)
                unit_of_work.commit()
                return write.artifact
        except BaseException:
            self.discard_uncommitted(writes)
            raise

    def put_bytes_current(
        self,
        connection: object,
        data: bytes,
        *,
        media_type: str,
        redaction: str,
    ) -> ArtifactRefV1:
        return self.put_bytes_tracked_current(
            connection,
            data,
            media_type=media_type,
            redaction=redaction,
        ).artifact

    def put_bytes_tracked_current(
        self,
        connection: object,
        data: bytes,
        *,
        media_type: str,
        redaction: str,
    ) -> ArtifactWriteReceipt:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("artifact connection must be SQLite")
        if not isinstance(data, bytes):
            raise TypeError("artifact data must be bytes")
        if not media_type.strip() or len(media_type) > 255:
            raise ValueError("artifact media_type is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", redaction):
            raise ValueError("artifact redaction must be a stable code")
        self._reject_secret(data)
        if len(data) > self._max_object_bytes:
            raise ArtifactQuotaExceeded("artifact object quota exceeded")
        digest = hashlib.sha256(data).hexdigest()
        artifact = ArtifactRefV1(
            digest=digest,
            size_bytes=len(data),
            media_type=media_type,
            redaction=redaction,
            uri=f"artifact://sha256/{digest}",
            root_fingerprint=self._root_fingerprint,
        )
        with self._artifact_root_lock():
            return self._put_bytes_tracked_current_locked(connection, data, artifact)

    def _put_bytes_tracked_current_locked(
        self,
        connection: sqlite3.Connection,
        data: bytes,
        artifact: ArtifactRefV1,
    ) -> ArtifactWriteReceipt:
        digest = artifact.digest
        existing = self._repository.get_current(connection, digest)
        if existing is not None:
            if existing.root_fingerprint != self._root_fingerprint:
                raise ArtifactIntegrityError("artifact is bound to a different root")
            if existing != artifact:
                raise ArtifactIntegrityError("artifact metadata conflicts with its digest")
            if self._read_verified(existing) != data:
                raise ArtifactIntegrityError("artifact digest mismatch")
            return ArtifactWriteReceipt(artifact=existing, created_file_identity=None)
        total = self._repository.total_bytes_current(connection, self._root_fingerprint)
        if total + len(data) > self._max_total_bytes:
            raise ArtifactQuotaExceeded("artifact total quota exceeded")
        path = self._path(digest, create_parent=True)
        if path.exists() or path.is_symlink():
            if self._read_verified(artifact) != data:
                raise ArtifactIntegrityError("artifact digest mismatch")
            self._repository.add_current(connection, artifact, self._clock().isoformat())
            return ArtifactWriteReceipt(artifact=artifact, created_file_identity=None)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
        created_file_identity: tuple[int, int] | None = None
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_name, path)
                stat_result = path.stat(follow_symlinks=False)
                created_file_identity = (stat_result.st_dev, stat_result.st_ino)
            except FileExistsError:
                if self._read_verified(artifact) != data:
                    raise ArtifactIntegrityError("artifact digest mismatch") from None
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            if created_file_identity is not None:
                self._discard_created_file_locked(artifact, created_file_identity)
            raise
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        try:
            self._repository.add_current(connection, artifact, self._clock().isoformat())
        except BaseException:
            if created_file_identity is not None:
                self._discard_created_file_locked(artifact, created_file_identity)
            raise
        return ArtifactWriteReceipt(
            artifact=artifact,
            created_file_identity=created_file_identity,
        )

    def discard_uncommitted(self, writes: list[ArtifactWriteReceipt]) -> None:
        """Delete only files created by rolled-back writes without durable metadata."""

        seen: set[str] = set()
        for write in writes:
            identity = write.created_file_identity
            digest = write.artifact.digest
            if identity is None or digest in seen:
                continue
            seen.add(digest)
            if not self._has_durable_metadata(digest):
                self._discard_created_file(write.artifact, identity)

    def _has_durable_metadata(self, digest: str) -> bool:
        """Read committed metadata without depending on a possibly failing commit path."""

        try:
            with self._unit_of_work_factory() as unit_of_work:
                value = self._repository.get_current(unit_of_work.connection, digest)
                unit_of_work.rollback()
                return value is not None
        except BaseException:
            # Cleanup must never risk deleting bytes whose durable ownership
            # cannot be established.
            return True

    def _discard_created_file(self, artifact: ArtifactRefV1, identity: tuple[int, int]) -> None:
        with self._artifact_root_lock():
            self._discard_created_file_locked(artifact, identity)

    def _discard_created_file_locked(
        self, artifact: ArtifactRefV1, identity: tuple[int, int]
    ) -> None:
        path = self._path(artifact.digest)
        try:
            stat_result = path.stat(follow_symlinks=False)
            if (stat_result.st_dev, stat_result.st_ino) != identity:
                return
            self._read_verified(artifact)
            path.unlink()
        except (ArtifactStoreError, FileNotFoundError, OSError):
            return

    @contextmanager
    def _artifact_root_lock(self) -> Iterator[None]:
        """Serialize protocol-compliant artifact-root writers on POSIX targets."""

        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self._lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("artifact root lock is not a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise ArtifactIntegrityError("artifact root lock is unavailable") from exc
        assert descriptor is not None
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _read_verified(self, artifact: ArtifactRefV1) -> bytes:
        if artifact.root_fingerprint != self._root_fingerprint:
            raise ArtifactIntegrityError("artifact is bound to a different root")
        path = self._path(artifact.digest)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ArtifactIntegrityError("artifact bytes are unavailable") from exc
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                data = stream.read(self._max_object_bytes + 1)
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise
        if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.digest:
            raise ArtifactIntegrityError("artifact digest mismatch")
        return data

    def get_bytes(self, digest: str) -> bytes:
        if _DIGEST.fullmatch(digest) is None:
            raise ValueError("artifact digest must be 64 lowercase hexadecimal characters")
        artifact = self._repository.get(digest)
        if artifact is None:
            raise ArtifactIntegrityError("artifact metadata not found")
        return self._read_verified(artifact)

    def verify(self, digest: str) -> bool:
        try:
            self.get_bytes(digest)
        except (ArtifactRepositoryError, ArtifactStoreError, OSError, ValueError):
            return False
        return True

    def verify_ref(self, artifact: ArtifactRefV1) -> bool:
        try:
            durable = self._repository.get(artifact.digest)
            if durable is None or durable != artifact:
                return False
            self._read_verified(durable)
        except (ArtifactRepositoryError, ArtifactStoreError, OSError, ValueError):
            return False
        return True

    def verify_ref_current(self, artifact: ArtifactRefV1) -> bool:
        try:
            self._read_verified(artifact)
        except (ArtifactStoreError, OSError, ValueError):
            return False
        return True


class EvidenceService:
    """Build and close one authoritative, immutable snapshot per Memorial."""

    _MAX_EXPORT_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        storage: _EvidenceStorage,
        artifacts: ArtifactStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._artifacts = artifacts
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _bundle_id(memorial_id: str) -> str:
        digest = hashlib.sha256(memorial_id.encode()).hexdigest()[:32]
        return f"evidence:{digest}"

    @staticmethod
    def _json_object(raw: object, field: str) -> dict[str, object]:
        if not isinstance(raw, str):
            raise EvidenceServiceError(f"{field} is not text")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceServiceError(f"{field} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise EvidenceServiceError(f"{field} must be a JSON object")
        return value

    @staticmethod
    def _nonnegative_int(value: object, field: str) -> int:
        if type(value) is not int or value < 0:
            raise EvidenceServiceError(f"{field} must be a non-negative integer")
        return value

    @staticmethod
    def _lock_hash() -> str:
        # The dependency lock is not a packaged runtime resource. Keep source and
        # wheel evidence identical by recording the established unavailable value.
        return "0" * 64

    def _environment(
        self,
        effective: EffectiveGovernanceContractV1,
    ) -> EnvironmentEvidenceV1:
        lock_hash = self._lock_hash()
        facts = {
            "architecture": platform.machine() or "unknown",
            "dependency_lock_hash": lock_hash,
            "platform": platform.system() or "unknown",
            "python_version": platform.python_version(),
            "tianshu_version": "0.4.2",
            "workspace_base_revision": effective.resolved_base_revision,
        }
        return EnvironmentEvidenceV1(
            tianshu_version="0.4.2",
            python_version=platform.python_version(),
            platform=platform.system() or "unknown",
            architecture=platform.machine() or "unknown",
            dependency_lock_hash=lock_hash,
            workspace_base_revision=effective.resolved_base_revision,
            environment_fingerprint=hashlib.sha256(canonical_json_bytes(facts)).hexdigest(),
        )

    @staticmethod
    def _workspace_ref(source_id: str | None) -> str:
        if source_id is None:
            return "workspace:unspecified"
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", source_id)
            and redact_text(source_id) == source_id
            and not contains_raw_sensitive_payload(source_id)
        ):
            return source_id
        digest = hashlib.sha256(source_id.encode()).hexdigest()[:32]
        return f"workspace:{digest}"

    @staticmethod
    def _checks(
        connection: sqlite3.Connection,
        memorial_id: str,
        requested: RequestedGovernanceContractV1,
    ) -> tuple[CheckEvidenceV1, ...]:
        rows = connection.execute(
            """
            SELECT payload_json, created_at
            FROM events
            WHERE memorial_id=? AND event_type='acceptance.check.completed'
            ORDER BY created_at, id
            """,
            (memorial_id,),
        ).fetchall()
        specifications = {check.name: check for check in requested.acceptance.checks}
        checks: dict[str, CheckEvidenceV1] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                name = str(payload["name"])
                specification = specifications[name]
                status = str(payload["status"])
                if status not in {"passed", "failed", "unavailable", "skipped"}:
                    continue
                exit_code = payload.get("exit_code")
                output_digest = payload.get("output_artifact_digest")
                started_at = payload.get("started_at", row["created_at"])
                completed_at = payload.get("completed_at", row["created_at"])
                checks[name] = CheckEvidenceV1(
                    check_id=f"check:{hashlib.sha256(name.encode()).hexdigest()[:32]}",
                    name=name,
                    status=cast(
                        Literal["passed", "failed", "unavailable", "skipped"],
                        status,
                    ),
                    command_fingerprint=specification.content_hash,
                    exit_code=exit_code,
                    output_artifact_digest=output_digest,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
                continue
        return tuple(checks[name] for name in sorted(checks))

    @staticmethod
    def _decisions(
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> tuple[tuple[str, ...], tuple[DecisionEvidenceV1, ...]]:
        rows = connection.execute(
            """
            SELECT request.decision_request_id, request.kind, request.payload_hash,
                   resolution.action, resolution.reason,
                   resolution.actor_principal_id, resolution.resolved_at
            FROM decision_requests AS request
            LEFT JOIN decision_resolutions AS resolution
              ON resolution.decision_request_id=request.decision_request_id
            WHERE request.memorial_id=?
            ORDER BY request.created_at, request.decision_request_id
            """,
            (memorial_id,),
        ).fetchall()
        required = tuple(str(row["decision_request_id"]) for row in rows)
        evidence: list[DecisionEvidenceV1] = []
        for row in rows:
            if row["action"] is None:
                continue
            reason = redact_text(str(row["reason"]))
            evidence.append(
                DecisionEvidenceV1.model_validate_json(
                    json.dumps(
                        {
                            "decision_request_id": row["decision_request_id"],
                            "kind": row["kind"],
                            "action": row["action"],
                            "actor_principal_id": row["actor_principal_id"],
                            "reason": reason,
                            "payload_hash": row["payload_hash"],
                            "resolved_at": row["resolved_at"],
                        }
                    )
                )
            )
        return required, tuple(evidence)

    @staticmethod
    def _effects(
        connection: sqlite3.Connection,
        memorial_id: str,
        side_effect_cursor: int,
    ) -> tuple[tuple[str, ...], tuple[EffectEvidenceV1, ...]]:
        rows = connection.execute(
            """
            SELECT intent_id, effect_id, sequence_no, status, semantics,
                   request_hash, result_hash, reason_code
            FROM side_effect_journal
            WHERE memorial_id=?
            ORDER BY sequence_no, intent_id
            """,
            (memorial_id,),
        ).fetchall()
        required = [str(row["intent_id"]) for row in rows]
        positions = {int(row["sequence_no"]) for row in rows}
        required.extend(
            f"cursor:{position}"
            for position in range(side_effect_cursor)
            if position not in positions
        )
        evidence = tuple(
            EffectEvidenceV1(
                intent_id=row["intent_id"],
                effect_id=row["effect_id"],
                status=row["status"],
                semantics=row["semantics"],
                request_hash=row["request_hash"],
                result_hash=row["result_hash"],
                reason_code=row["reason_code"],
            )
            for row in rows
        )
        return tuple(required), evidence

    def _snapshot_current(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> tuple[str, EvidenceSnapshotV1]:
        row = connection.execute(
            """
            SELECT memorial.edict_id, memorial.status, memorial.audit_json,
                   memorial.usage_json, memorial.final_output, memorial.result,
                   requested.contract_json AS requested_json,
                   requested.contract_hash AS requested_hash,
                   effective.contract_json AS effective_json,
                   effective.contract_hash AS effective_hash
            FROM memorials AS memorial
            JOIN requested_governance_contracts AS requested
              ON requested.edict_id=memorial.edict_id
            LEFT JOIN effective_governance_contracts AS effective
              ON effective.memorial_id=memorial.id
            WHERE memorial.id=?
            """,
            (memorial_id,),
        ).fetchone()
        if row is None:
            raise EvidenceNotFound("Memorial does not exist")
        if row["effective_json"] is None:
            raise EvidenceIncompleteError(("contract:effective",))
        requested = RequestedGovernanceContractV1.model_validate_json(row["requested_json"])
        effective = EffectiveGovernanceContractV1.model_validate_json(row["effective_json"])
        if requested.content_hash != row["requested_hash"]:
            raise EvidenceServiceError("requested governance contract hash mismatch")
        if effective.content_hash != row["effective_hash"]:
            raise EvidenceServiceError("effective governance contract hash mismatch")
        manifest = get_executor_manifest(effective.executor.adapter_id)
        evidence_manifest = ExecutorManifestEvidenceV1.model_validate_json(
            canonical_json_bytes(manifest)
        )
        if evidence_manifest.content_hash != manifest.content_hash:
            raise EvidenceServiceError("executor manifest is not canonically round-trippable")
        state = self._storage.run_state_repo.load(connection, memorial_id)
        if state is None:
            raise EvidenceIncompleteError(("run_state",))
        continuation = agent_plan_continuation(state.continuation)
        if continuation is None:
            raise EvidenceIncompleteError(("plan_revision",))
        if not continuation.plan_revisions or continuation.plan_snapshot is None:
            raise EvidenceIncompleteError(("plan_revision",))
        plan_revision = continuation.plan_revisions[-1]
        plan_artifact = self._artifacts.put_bytes_current(
            connection,
            canonical_json_bytes(continuation.plan_snapshot),
            media_type="application/json",
            redaction="safe",
        )
        if plan_artifact.digest != plan_revision.artifact_digest:
            raise EvidenceServiceError("plan artifact digest mismatch")

        checks = self._checks(connection, memorial_id, requested)
        artifact_by_digest = {plan_artifact.digest: plan_artifact}
        required_artifact_digests = {plan_artifact.digest}
        for check in checks:
            if check.output_artifact_digest is None:
                continue
            required_artifact_digests.add(check.output_artifact_digest)
            output_artifact = self._storage.artifact_repo.get_current(
                connection,
                check.output_artifact_digest,
            )
            if output_artifact is not None:
                artifact_by_digest[output_artifact.digest] = output_artifact
        artifacts = tuple(artifact_by_digest[key] for key in sorted(artifact_by_digest))
        required_decisions, decisions = self._decisions(connection, memorial_id)
        required_effects, effects = self._effects(
            connection,
            memorial_id,
            state.side_effect_cursor,
        )
        requirements = EvidenceRequirementsV1(
            check_names=tuple(check.name for check in requested.acceptance.checks),
            decision_request_ids=required_decisions,
            effect_intent_ids=required_effects,
            artifact_digests=tuple(sorted(required_artifact_digests)),
        )
        missing = self._missing(requirements, artifacts, checks, decisions, effects)
        if state.phase not in {RunPhase.COMPLETED, RunPhase.FAILED}:
            missing.append("run_state:terminal")
        audit = self._json_object(row["audit_json"], "audit_json") if row["audit_json"] else None
        if audit is None or audit.get("verdict") != "pass":
            missing.append("auditor:legacy")
        missing = sorted(set(missing))
        auditor = AuditorConclusionV1(
            auditor_id="tianshu.independent.v1",
            verdict="fail" if missing else "pass",
            reason=(
                "missing mandatory evidence: " + ", ".join(missing)
                if missing
                else "independent evidence requirements satisfied"
            ),
            required_evidence=tuple(
                [f"check:{value}" for value in requirements.check_names]
                + [f"decision:{value}" for value in requirements.decision_request_ids]
                + [f"effect:{value}" for value in requirements.effect_intent_ids]
                + [f"artifact:{value}" for value in requirements.artifact_digests]
            ),
            missing_evidence=tuple(missing),
            evaluated_at=self._clock(),
        )
        usage = self._json_object(row["usage_json"], "usage_json")
        cost_row = connection.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(SUM(cost_cny), 0) AS cost,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt,
                   COALESCE(SUM(completion_tokens), 0) AS completion
            FROM cost_ledger WHERE memorial_id=?
            """,
            (memorial_id,),
        ).fetchone()
        has_cost_rows = int(cost_row["count"]) > 0
        prompt_tokens = self._nonnegative_int(
            cost_row["prompt"] if has_cost_rows else usage.get("prompt_tokens", 0),
            "prompt_tokens",
        )
        completion_tokens = self._nonnegative_int(
            cost_row["completion"] if has_cost_rows else usage.get("completion_tokens", 0),
            "completion_tokens",
        )
        cache_read_tokens = self._nonnegative_int(
            usage.get("cache_read_tokens", 0),
            "cache_read_tokens",
        )
        cost = CostEvidenceV1(
            requested_budget=requested.budget.cost_limit_cny,
            effective_budget=effective.budget.cost_limit_cny,
            actual_cost=Decimal(
                str(cost_row["cost"] if has_cost_rows else usage.get("cost_cny", 0))
            ),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        bundle_id = self._bundle_id(memorial_id)
        result = row["final_output"] or row["result"]
        expected_hash = hashlib.sha256(str(result).encode()).hexdigest() if result else None
        snapshot = EvidenceSnapshotV1(
            run_state_version=state.version,
            requested_contract=requested,
            requested_contract_hash=requested.content_hash,
            effective_contract=effective,
            effective_contract_hash=effective.content_hash,
            executor_manifest=evidence_manifest,
            executor_manifest_hash=evidence_manifest.content_hash,
            plan_revision=plan_revision,
            artifacts=artifacts,
            checks=checks,
            decisions=decisions,
            effects=effects,
            cost=cost,
            environment=self._environment(effective),
            auditor=auditor,
            requirements=requirements,
            reproduction_command=ReproductionCommandV1(
                label="replay through governed submission",
                argv=("tianshu", "evidence", "replay", bundle_id),
                cwd_ref=self._workspace_ref(effective.resolved_source_id),
                environment_keys=(),
                expected_result_hash=expected_hash,
            ),
        )
        return str(row["edict_id"]), snapshot

    def build_open(self, memorial_id: str) -> EvidenceBundleV1:
        with self._storage.unit_of_work() as unit_of_work:
            existing = self._storage.evidence_repo.get_for_memorial_current(
                unit_of_work.connection,
                memorial_id,
            )
            if isinstance(existing, ClosedEvidenceBundleV1):
                raise EvidenceServiceError("evidence bundle is already closed")
            if isinstance(existing, EvidenceBundleV1):
                unit_of_work.commit()
                return existing
            edict_id, snapshot = self._snapshot_current(unit_of_work.connection, memorial_id)
            bundle = EvidenceBundleV1(
                bundle_id=self._bundle_id(memorial_id),
                edict_id=edict_id,
                memorial_id=memorial_id,
                snapshot=snapshot,
                version=1,
                created_at=self._clock(),
            )
            self._storage.evidence_repo.add_open_current(unit_of_work.connection, bundle)
            unit_of_work.commit()
            return bundle

    @staticmethod
    def _missing(
        requirements: EvidenceRequirementsV1,
        artifacts: tuple[ArtifactRefV1, ...],
        checks: tuple[CheckEvidenceV1, ...],
        decisions: tuple[DecisionEvidenceV1, ...],
        effects: tuple[EffectEvidenceV1, ...],
    ) -> list[str]:
        available_artifacts = {item.digest for item in artifacts}
        passed_checks = {item.name for item in checks if item.status == "passed"}
        resolved_decisions = {item.decision_request_id for item in decisions}
        terminal_effects = {
            item.intent_id for item in effects if item.status in {"receipted", "uncertain"}
        }
        return (
            [
                f"artifact:{item}"
                for item in requirements.artifact_digests
                if item not in available_artifacts
            ]
            + [f"check:{item}" for item in requirements.check_names if item not in passed_checks]
            + [
                f"decision:{item}"
                for item in requirements.decision_request_ids
                if item not in resolved_decisions
            ]
            + [
                f"effect:{item}"
                for item in requirements.effect_intent_ids
                if item not in terminal_effects
            ]
        )

    @staticmethod
    def _required_evidence(requirements: EvidenceRequirementsV1) -> tuple[str, ...]:
        return tuple(
            [f"check:{value}" for value in requirements.check_names]
            + [f"decision:{value}" for value in requirements.decision_request_ids]
            + [f"effect:{value}" for value in requirements.effect_intent_ids]
            + [f"artifact:{value}" for value in requirements.artifact_digests]
        )

    @classmethod
    def _semantic_reasons(cls, snapshot: EvidenceSnapshotV1) -> tuple[str, ...]:
        missing = tuple(
            sorted(
                set(
                    cls._missing(
                        snapshot.requirements,
                        snapshot.artifacts,
                        snapshot.checks,
                        snapshot.decisions,
                        snapshot.effects,
                    )
                )
            )
        )
        reasons = [f"missing_required:{item}" for item in missing]
        if snapshot.auditor.required_evidence != cls._required_evidence(snapshot.requirements):
            reasons.append("auditor_required_evidence_mismatch")
        if snapshot.auditor.missing_evidence != missing:
            reasons.append("auditor_missing_evidence_mismatch")
        expected_verdict = "fail" if missing else "pass"
        if snapshot.auditor.verdict != expected_verdict:
            reasons.append("auditor_verdict_mismatch")
        return tuple(reasons)

    def _require_complete(self, snapshot: EvidenceSnapshotV1) -> None:
        missing = [
            reason.removeprefix("missing_required:") for reason in self._semantic_reasons(snapshot)
        ]
        for artifact in snapshot.artifacts:
            if not self._artifacts.verify_ref_current(artifact):
                missing.append(f"artifact:{artifact.digest}")
        if missing:
            raise EvidenceIncompleteError(tuple(missing))

    def close(self, memorial_id: str, *, expected_version: int) -> ClosedEvidenceBundleV1:
        with self._storage.unit_of_work() as unit_of_work:
            existing = self._storage.evidence_repo.get_for_memorial_current(
                unit_of_work.connection,
                memorial_id,
            )
            if isinstance(existing, ClosedEvidenceBundleV1):
                unit_of_work.commit()
                return existing
            if existing is None:
                edict_id, open_snapshot = self._snapshot_current(
                    unit_of_work.connection,
                    memorial_id,
                )
                existing = EvidenceBundleV1(
                    bundle_id=self._bundle_id(memorial_id),
                    edict_id=edict_id,
                    memorial_id=memorial_id,
                    snapshot=open_snapshot,
                    version=1,
                    created_at=self._clock(),
                )
                self._storage.evidence_repo.add_open_current(
                    unit_of_work.connection,
                    existing,
                )
            if existing.version != expected_version:
                raise EvidenceConflict("evidence close compare-and-swap conflict")
            edict_id, snapshot = self._snapshot_current(unit_of_work.connection, memorial_id)
            self._require_complete(snapshot)
            payload = {
                "schema_version": "1.0",
                "bundle_id": existing.bundle_id,
                "edict_id": edict_id,
                "memorial_id": memorial_id,
                "status": "closed",
                "snapshot": snapshot.model_dump(mode="json"),
                "version": expected_version + 1,
                "created_at": existing.created_at.isoformat().replace("+00:00", "Z"),
                "closed_at": self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            }
            closed_payload = {**payload, "content_hash": closed_bundle_content_hash(payload)}
            closed = ClosedEvidenceBundleV1.model_validate_json(
                canonical_json_bytes(closed_payload)
            )
            saved = self._storage.evidence_repo.close_current(
                unit_of_work.connection,
                closed,
                expected_version=expected_version,
            )
            unit_of_work.commit()
            return saved

    def export(self, bundle_id: str) -> bytes:
        bundle = self._storage.evidence_repo.get(bundle_id)
        if bundle is None:
            raise EvidenceNotFound("evidence bundle not found")
        if not isinstance(bundle, ClosedEvidenceBundleV1):
            raise EvidenceServiceError("evidence bundle is still open")
        return canonical_json_bytes(bundle)

    def verify(self, bundle_id: str) -> EvidenceVerificationV1:
        try:
            bundle = self._storage.evidence_repo.get(bundle_id)
        except EvidenceRepositoryError:
            return EvidenceVerificationV1(
                bundle_id=bundle_id,
                verified=False,
                content_hash="0" * 64,
                artifact_count=0,
                reason_codes=("persisted_snapshot_invalid",),
            )
        if not isinstance(bundle, ClosedEvidenceBundleV1):
            return EvidenceVerificationV1(
                bundle_id=bundle_id,
                verified=False,
                content_hash="0" * 64,
                artifact_count=0,
                reason_codes=("not_closed",),
            )
        return self._verification_for_bundle(bundle)

    def _verification_for_bundle(
        self,
        bundle: ClosedEvidenceBundleV1,
    ) -> EvidenceVerificationV1:
        reasons: list[str] = []
        if closed_bundle_content_hash(bundle) != bundle.content_hash:
            reasons.append("content_hash_mismatch")
        reasons.extend(self._semantic_reasons(bundle.snapshot))
        for artifact in bundle.snapshot.artifacts:
            if not self._artifacts.verify_ref(artifact):
                reasons.append(f"artifact_invalid:{artifact.digest}")
        return EvidenceVerificationV1(
            bundle_id=bundle.bundle_id,
            verified=not reasons,
            content_hash=bundle.content_hash,
            artifact_count=len(bundle.snapshot.artifacts),
            reason_codes=tuple(reasons or ("verified",)),
        )

    def verify_export(self, data: bytes) -> EvidenceVerificationV1:
        if not isinstance(data, bytes) or len(data) > self._MAX_EXPORT_BYTES:
            raise EvidenceImportError("evidence export exceeds the bounded input size")
        bundle_id = "invalid:export"
        content_hash = "0" * 64
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError("export must be an object")
            bundle_id = str(payload.get("bundle_id", bundle_id))
            raw_hash = payload.get("content_hash")
            if isinstance(raw_hash, str) and re.fullmatch(r"[0-9a-f]{64}", raw_hash):
                content_hash = raw_hash
            if closed_bundle_content_hash(payload) != content_hash:
                return EvidenceVerificationV1(
                    bundle_id=bundle_id,
                    verified=False,
                    content_hash=content_hash,
                    artifact_count=0,
                    reason_codes=("content_hash_mismatch",),
                )
            bundle = ClosedEvidenceBundleV1.model_validate_json(data)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
            return EvidenceVerificationV1(
                bundle_id=bundle_id,
                verified=False,
                content_hash=content_hash,
                artifact_count=0,
                reason_codes=("schema_invalid",),
            )
        return self._verification_for_bundle(bundle)

    def import_bundle(self, data: bytes) -> ClosedEvidenceBundleV1:
        verification = self.verify_export(data)
        if not verification.verified:
            raise EvidenceImportError(
                "evidence import verification failed: " + ", ".join(verification.reason_codes)
            )
        return ClosedEvidenceBundleV1.model_validate_json(data)


__all__ = [
    "ArtifactIntegrityError",
    "ArtifactQuotaExceeded",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactWriteReceipt",
    "EvidenceImportError",
    "EvidenceIncompleteError",
    "EvidenceNotFound",
    "EvidenceService",
    "EvidenceServiceError",
]
