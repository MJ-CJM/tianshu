"""Single authenticated authority for governed canary, promotion, and rollback."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tianshu.evidence.service import ArtifactStore, ArtifactStoreError
from tianshu.evolution.adapters.base import (
    ActivationReceiptV1,
    AdapterError,
    AdapterOperationUnavailable,
)
from tianshu.evolution.adapters.base import (
    RollbackReceiptV1 as AdapterRollbackReceiptV1,
)
from tianshu.evolution.adapters.skill import SkillCandidateAdapter
from tianshu.evolution.gates import EvolutionGateReportV1
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.events import make_event
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    EvolutionCandidateV1,
    RoutingPolicyV1,
)
from tianshu.models.principal import AuthContext
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.evolution_repo import EvolutionRepository, EvolutionRepositoryConflict
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
from tianshu.storage.unit_of_work import SqliteUnitOfWork

_AT_FDCWD = getattr(os, "AT_FDCWD", -2)
_RENAME_EXCHANGE = 0x00000002
_RENAME_NOFOLLOW_ANY = 0x00000010


def _atomic_exchange(source: Path, target: Path) -> None:
    """Atomically exchange two existing paths or fail without changing either."""

    libc = ctypes.CDLL(None, use_errno=True)
    encoded_source = os.fsencode(source)
    encoded_target = os.fsencode(target)
    if sys.platform == "darwin":
        try:
            operation = libc.renameatx_np
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "atomic directory exchange unavailable") from exc
        operation.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        operation.restype = ctypes.c_int
        result = operation(
            _AT_FDCWD,
            encoded_source,
            _AT_FDCWD,
            encoded_target,
            _RENAME_EXCHANGE | _RENAME_NOFOLLOW_ANY,
        )
    elif sys.platform.startswith("linux"):
        try:
            operation = libc.renameat2
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "atomic directory exchange unavailable") from exc
        operation.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        operation.restype = ctypes.c_int
        result = operation(
            _AT_FDCWD,
            encoded_source,
            _AT_FDCWD,
            encoded_target,
            _RENAME_EXCHANGE,
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic directory exchange unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, "atomic directory exchange failed")


def _preflight_atomic_exchange(root: Path) -> None:
    """Prove exchange support on the live filesystem before staging a mutation."""

    left = Path(tempfile.mkdtemp(prefix=".atomic-exchange-probe-a-", dir=root))
    right: Path | None = None
    try:
        right = Path(tempfile.mkdtemp(prefix=".atomic-exchange-probe-b-", dir=root))
        (left / "left").write_text("left", encoding="utf-8")
        (right / "right").write_text("right", encoding="utf-8")
        _atomic_exchange(left, right)
        if not (left / "right").is_file() or not (right / "left").is_file():
            raise OSError(errno.EIO, "atomic directory exchange verification failed")
    finally:
        try:
            shutil.rmtree(left, ignore_errors=False)
        finally:
            if right is not None:
                shutil.rmtree(right, ignore_errors=False)


class PromotionError(RuntimeError):
    """Stable, redacted failure at the governed promotion boundary."""


class PromotionConflict(PromotionError):
    """The command conflicts with durable promotion state or preconditions."""


class PromotionAuthorizationError(PromotionError):
    """The caller is not authorized to mutate governed evolution state."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class _Command(_StrictModel):
    schema_version: Literal[1] = 1
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(max_length=200)
    reason: str = Field(max_length=1000)

    _validate_text = field_validator("idempotency_key", "reason")(_non_blank)


class StartCanaryCommand(_Command):
    allocation_basis_points: int = Field(gt=0, le=10_000)
    allocation_seed_id: str = Field(max_length=256)
    decision_request_id: str | None = Field(default=None, max_length=256)

    _validate_seed = field_validator("allocation_seed_id")(_non_blank)


class PromoteCommand(_Command):
    decision_request_id: str | None = Field(default=None, max_length=256)


class RollbackCommand(_Command):
    decision_request_id: str | None = Field(default=None, max_length=256)


class PromotionReceiptV1(_StrictModel):
    schema_version: Literal[1] = 1
    action: Literal["start_canary", "promote"]
    status: Literal["completed"] = "completed"
    idempotency_key: str
    journal_id: str
    candidate_id: str
    candidate_version: int = Field(ge=1)
    gate_snapshot_version: int = Field(ge=1)
    gate_report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lifecycle: CandidateLifecycle
    routing_version: int = Field(ge=1)
    allocation_basis_points: int = Field(ge=0, le=10_000)
    effect_artifact_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    completed_at: datetime


class RollbackReceiptV1(_StrictModel):
    schema_version: Literal[1] = 1
    action: Literal["rollback"] = "rollback"
    status: Literal["completed"] = "completed"
    idempotency_key: str
    journal_id: str
    candidate_id: str
    candidate_version: int = Field(ge=1)
    lifecycle: Literal[CandidateLifecycle.ROLLED_BACK]
    routing_version: int = Field(ge=1)
    allocation_basis_points: Literal[0] = 0
    effect_artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_at: datetime


class _Storage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...


class _GateAuthority(Protocol):
    def get_current_report_current(
        self, connection: sqlite3.Connection, candidate_id: str
    ) -> EvolutionGateReportV1 | None: ...

    def validate_bound_green_report_current(
        self,
        connection: sqlite3.Connection,
        candidate_id: str,
        *,
        candidate_version: int,
        gate_snapshot_version: int,
        candidate_digest: str,
        report_hash: str,
    ) -> EvolutionGateReportV1: ...


class _Adapter(Protocol):
    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1: ...

    def rollback(self, candidate: EvolutionCandidateV1) -> AdapterRollbackReceiptV1: ...


class UnavailablePromotionAdapter:
    """Explicit fail-closed capability for kinds without a safe live writer yet."""

    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1:
        del candidate
        raise AdapterOperationUnavailable("candidate activation is unavailable")

    def rollback(self, candidate: EvolutionCandidateV1) -> AdapterRollbackReceiptV1:
        del candidate
        raise AdapterOperationUnavailable("candidate rollback is unavailable")


class SkillPromotionAdapter:
    """Idempotently replace one validated live skill with an artifact-backed package."""

    def __init__(self, artifacts: ArtifactStore, *, live_root: Path) -> None:
        self._artifacts = artifacts
        Path(live_root).mkdir(parents=True, exist_ok=True, mode=0o700)
        self._live_root = Path(live_root).resolve(strict=True)
        self._validator = SkillCandidateAdapter(artifacts, live_root=self._live_root)
        self._lock_path = self._live_root / ".promotion.lock"
        self._exchange_ready = False

    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1:
        if candidate.kind is not CandidateKind.SKILL:
            raise AdapterError("skill promotion kind mismatch")
        if candidate.lifecycle is not CandidateLifecycle.CANARY:
            raise AdapterError("skill activation requires canary candidate")
        self._apply(candidate, digest=candidate.candidate.artifact_digest)
        return ActivationReceiptV1(
            candidate_id=candidate.candidate_id,
            artifact_digest=candidate.candidate.artifact_digest,
        )

    def rollback(self, candidate: EvolutionCandidateV1) -> AdapterRollbackReceiptV1:
        if candidate.kind is not CandidateKind.SKILL:
            raise AdapterError("skill rollback kind mismatch")
        if candidate.lifecycle is not CandidateLifecycle.ROLLBACK_PENDING:
            raise AdapterError("skill rollback requires rollback_pending candidate")
        self._apply(candidate, digest=candidate.base.artifact_digest)
        return AdapterRollbackReceiptV1(
            candidate_id=candidate.candidate_id,
            artifact_digest=candidate.base.artifact_digest,
        )

    def _apply(self, candidate: EvolutionCandidateV1, *, digest: str) -> None:
        try:
            package = self._load_package(digest)
            name = package.get("name")
            if not isinstance(name, str) or candidate.subject_key != f"skill:{name}":
                raise ValueError("candidate subject does not match skill package")
            fallback_digest = (
                candidate.base.artifact_digest
                if digest == candidate.candidate.artifact_digest
                else candidate.candidate.artifact_digest
            )
            fallback = self._load_package(fallback_digest)
            if fallback.get("name") != name:
                raise ValueError("skill fallback subject does not match candidate")
            target = self._live_root / name
            if target.parent != self._live_root or target.name != name:
                raise ValueError("skill target is not canonical")
            with self._locked():
                self._replace(target, package, fallback=fallback, digest=digest)
        except (ArtifactStoreError, AdapterError, OSError, TypeError, ValueError):
            raise AdapterError("skill promotion artifact apply failed") from None

    def _load_package(self, digest: str) -> dict[str, object]:
        raw = self._artifacts.get_bytes(digest)
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or canonical_json_bytes(payload) != raw
            or canonical_sha256(payload) != digest
        ):
            raise ValueError("candidate artifact is not canonical")
        return self._validator._normalize_domain(payload)  # noqa: SLF001

    @contextmanager
    def _locked(self):
        import fcntl

        descriptor = os.open(
            self._lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _replace(
        self,
        target: Path,
        package: dict[str, object],
        *,
        fallback: dict[str, object],
        digest: str,
    ) -> None:
        backup = self._live_root / f".promotion-backup-{target.name}"
        stage = self._live_root / f".promotion-stage-{target.name}-{digest}"
        self._reconcile(target, package, fallback=fallback, backup=backup, stage=stage)
        if self._matches(target, package):
            return
        if not self._matches(target, fallback):
            raise ValueError("live skill does not match the governed fallback")
        state = package.get("state")
        if state == "absent":
            if target.exists() or target.is_symlink():
                os.replace(target, stage)
                self._remove_tree(stage)
            return
        if state != "present":
            raise ValueError("skill package state is invalid")
        target_present = target.exists() or target.is_symlink()
        if target_present:
            self._ensure_atomic_exchange()
        self._prepare_stage(stage, package)
        if target_present:
            _atomic_exchange(stage, target)
            if not self._matches(target, package) or not self._matches(stage, fallback):
                raise ValueError("atomic skill exchange did not preserve exact trees")
        else:
            os.replace(stage, target)
        self._remove_tree(stage)

    def _ensure_atomic_exchange(self) -> None:
        if not self._exchange_ready:
            _preflight_atomic_exchange(self._live_root)
            self._exchange_ready = True

    def _prepare_stage(self, stage: Path, package: dict[str, object]) -> None:
        if stage.exists() or stage.is_symlink():
            if not self._matches(stage, package):
                raise ValueError("skill stage recovery state is not governed")
            return
        stage.mkdir(mode=0o700)
        try:
            self._materialize(stage, package)
            if not self._matches(stage, package):
                raise ValueError("staged skill package verification failed")
        except Exception:
            self._remove_tree(stage)
            raise

    def _reconcile(
        self,
        target: Path,
        package: dict[str, object],
        *,
        fallback: dict[str, object],
        backup: Path,
        stage: Path,
    ) -> None:
        stages = tuple(self._live_root.glob(f".promotion-stage-{target.name}-*"))
        target_present = target.exists() or target.is_symlink()
        backup_present = backup.exists() or backup.is_symlink()

        if backup_present:
            backup_is_desired = self._matches(backup, package)
            backup_is_fallback = self._matches(backup, fallback)
            if not (backup_is_desired or backup_is_fallback):
                raise ValueError("skill backup recovery state is not governed")
            if target_present or package.get("state") == "absent":
                self._remove_tree(backup)
            elif backup_is_fallback:
                os.replace(backup, target)
            else:
                raise ValueError("skill backup recovery state is ambiguous")

        target_is_desired = self._matches(target, package)
        target_is_fallback = self._matches(target, fallback)
        if not (target_is_desired or target_is_fallback):
            raise ValueError("live skill recovery state is not governed")
        for residue in stages:
            residue_is_desired = self._matches(residue, package)
            residue_is_fallback = self._matches(residue, fallback)
            if not (residue_is_desired or residue_is_fallback):
                raise ValueError("skill stage recovery state is not governed")
            if target_is_desired or residue != stage or residue_is_fallback:
                self._remove_tree(residue)

    @staticmethod
    def _materialize(stage: Path, package: dict[str, object]) -> None:
        members = package.get("members")
        if not isinstance(members, list):
            raise ValueError("skill package members are invalid")
        for raw_member in members:
            if not isinstance(raw_member, dict):
                raise ValueError("skill package member is invalid")
            relative = raw_member.get("path")
            kind = raw_member.get("kind")
            content = raw_member.get("content")
            if not isinstance(relative, str):
                raise ValueError("skill package path is invalid")
            target = stage.joinpath(*relative.split("/"))
            if kind == "directory":
                target.mkdir(parents=True, exist_ok=True)
            elif kind == "file" and isinstance(content, str):
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                raise ValueError("skill package member kind is invalid")

    @staticmethod
    def _matches(root: Path, package: dict[str, object]) -> bool:
        state = package.get("state")
        if state == "absent":
            return not root.exists() and not root.is_symlink()
        if state != "present" or not root.is_dir() or root.is_symlink():
            return False
        members = package.get("members")
        if not isinstance(members, list):
            return False
        expected_files: dict[str, str] = {}
        expected_dirs: set[str] = set()
        for member in members:
            if not isinstance(member, dict) or not isinstance(member.get("path"), str):
                return False
            relative = cast(str, member["path"])
            parts = relative.split("/")
            expected_dirs.update("/".join(parts[:index]) for index in range(1, len(parts)))
            if member.get("kind") == "directory":
                expected_dirs.add(relative)
            elif member.get("kind") == "file" and isinstance(member.get("content"), str):
                expected_files[relative] = cast(str, member["content"])
            else:
                return False
        actual_files: set[str] = set()
        actual_dirs: set[str] = set()
        try:
            for path in root.rglob("*"):
                if path.is_symlink():
                    return False
                relative = path.relative_to(root).as_posix()
                if path.is_dir():
                    actual_dirs.add(relative)
                elif path.is_file():
                    actual_files.add(relative)
                    if path.read_text(encoding="utf-8") != expected_files.get(relative):
                        return False
                else:
                    return False
        except (OSError, UnicodeError):
            return False
        return actual_files == set(expected_files) and actual_dirs == expected_dirs

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)


class _JournalEntry(_StrictModel):
    schema_version: Literal[1] = 1
    command_key: str
    candidate_id: str
    action: Literal["start_canary", "promote", "rollback"]
    status: Literal["intended", "applied", "rollback_pending", "completed"]
    decision_request_id: str | None
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    principal_id: str
    reason: str
    pre_transition_candidate_version: int = Field(ge=1)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_snapshot_version: int = Field(ge=1)
    gate_report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    routing_version: int | None = Field(default=None, ge=1)
    allocation_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    receipt: dict[str, object] | None = None


def _command_key(auth: AuthContext, idempotency_key: str) -> str:
    identity = canonical_sha256(
        {"principal_id": auth.principal.id, "idempotency_key": idempotency_key}
    )
    return f"promotion:{identity}"


def _request_hash(candidate_id: str, action: str, command: _Command) -> str:
    return canonical_sha256(
        {
            "candidate_id": candidate_id,
            "action": action,
            "command": command.model_dump(mode="json"),
        }
    )


def _journal_id(command_key: str, status: str) -> str:
    return hashlib.sha256(f"{command_key}\0{status}".encode()).hexdigest()


_JOURNAL_COLUMNS = """
promotion_journal_id, command_key, candidate_id, candidate_version,
gate_snapshot_version, action, status, decision_request_id, entry_json, entry_hash
"""


class PromotionService:
    """Own every governed canary, promote, and rollback mutation."""

    def __init__(
        self,
        storage: _Storage,
        gates: _GateAuthority,
        *,
        adapter_resolver: Callable[[CandidateKind], _Adapter],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._gates = gates
        self._adapter_resolver = adapter_resolver
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = EvolutionRepository()
        self._outbox = OutboxRepository()

    def start_canary(
        self, candidate_id: str, command: StartCanaryCommand, *, auth: AuthContext
    ) -> PromotionReceiptV1:
        self._authorize(auth)
        request_hash = _request_hash(candidate_id, "start_canary", command)
        command_key = _command_key(auth, command.idempotency_key)
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            candidate = self._get_authorized_candidate(connection, candidate_id, auth=auth)
            existing = self._completed_receipt(
                connection,
                command_key=command_key,
                request_hash=request_hash,
                receipt_type=PromotionReceiptV1,
                candidate=candidate,
                action="start_canary",
                command=command,
                auth=auth,
            )
            if existing is not None:
                unit_of_work.commit()
                return existing
            self._require_expected_version(candidate, command.expected_version)
            if candidate.lifecycle is not CandidateLifecycle.READY:
                raise PromotionConflict("promotion_preconditions_not_met")
            try:
                report = self._gates.get_current_report_current(connection, candidate_id)
            except EvolutionRepositoryConflict as exc:
                raise PromotionConflict("gate_snapshot_conflict") from exc
            if (
                report is None
                or not report.promotion_allowed
                or report.candidate_id != candidate.candidate_id
                or report.candidate_version != candidate.version
                or report.candidate_digest != candidate.candidate.artifact_digest
                or report.gate_snapshot_version != candidate.gate_snapshot_version
            ):
                raise PromotionConflict("gate_snapshot_conflict")
            if command.allocation_basis_points > (
                candidate.evolution_contract.max_canary_allocation_basis_points
            ):
                raise PromotionConflict("allocation_exceeds_contract")
            if self._routing_row(connection, candidate_id) is not None:
                raise PromotionConflict("promotion_preconditions_not_met")
            now = self._now()
            routing = RoutingPolicyV1(
                allocation_basis_points=command.allocation_basis_points,
                allocation_seed_id=command.allocation_seed_id,
                routing_version=1,
            )
            self._insert_routing(connection, candidate_id, routing, now=now)
            durable = self._repository.save_candidate(
                connection,
                candidate.model_copy(
                    update={
                        "routing": routing,
                        "lifecycle": CandidateLifecycle.CANARY,
                        "updated_at": now,
                    }
                ),
                expected_version=candidate.version,
            )
            receipt = PromotionReceiptV1(
                action="start_canary",
                idempotency_key=command.idempotency_key,
                journal_id=_journal_id(command_key, "completed"),
                candidate_id=candidate_id,
                candidate_version=durable.version,
                gate_snapshot_version=report.gate_snapshot_version,
                gate_report_hash=report.report_hash,
                lifecycle=durable.lifecycle,
                routing_version=routing.routing_version,
                allocation_basis_points=routing.allocation_basis_points,
                completed_at=now,
            )
            entry = _JournalEntry(
                command_key=command_key,
                candidate_id=candidate_id,
                action="start_canary",
                status="completed",
                decision_request_id=command.decision_request_id,
                request_hash=request_hash,
                principal_id=auth.principal.id,
                reason=command.reason,
                pre_transition_candidate_version=candidate.version,
                candidate_digest=candidate.candidate.artifact_digest,
                base_digest=candidate.base.artifact_digest,
                gate_snapshot_version=report.gate_snapshot_version,
                gate_report_hash=report.report_hash,
                routing_version=routing.routing_version,
                allocation_basis_points=routing.allocation_basis_points,
                receipt=receipt.model_dump(mode="json"),
            )
            self._append_journal(
                connection,
                command_key=command_key,
                candidate=candidate,
                decision_request_id=command.decision_request_id,
                entry=entry,
                now=now,
            )
            self._record(
                connection,
                candidate=durable,
                auth=auth,
                action="start_canary",
                routing=routing,
            )
            unit_of_work.commit()
            return receipt

    def promote(
        self, candidate_id: str, command: PromoteCommand, *, auth: AuthContext
    ) -> PromotionReceiptV1:
        self._authorize(auth)
        request_hash = _request_hash(candidate_id, "promote", command)
        command_key = _command_key(auth, command.idempotency_key)
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            candidate = self._get_authorized_candidate(connection, candidate_id, auth=auth)
            existing = self._completed_receipt(
                connection,
                command_key=command_key,
                request_hash=request_hash,
                receipt_type=PromotionReceiptV1,
                candidate=candidate,
                action="promote",
                command=command,
                auth=auth,
            )
            if existing is not None:
                unit_of_work.commit()
                return existing
            applied_entry = self._load_journal(connection, command_key, "applied")
            if applied_entry is not None:
                self._require_command_binding(
                    applied_entry,
                    candidate=candidate,
                    command_key=command_key,
                    action="promote",
                    command=command,
                    request_hash=request_hash,
                    auth=auth,
                )
            self._require_expected_version(candidate, command.expected_version)
            if candidate.lifecycle is not CandidateLifecycle.CANARY:
                raise PromotionConflict("promotion_preconditions_not_met")
            start = self._require_start_binding(connection, candidate)
            self._validate_bound_report(connection, candidate, start)
            self._require_routing_matches(connection, candidate, start)
            if candidate.kind is CandidateKind.CODE:
                try:
                    self._repository.require_code_promotion_decision(
                        connection,
                        candidate=candidate,
                        decision_request_id=command.decision_request_id,
                    )
                except EvolutionRepositoryConflict as exc:
                    raise PromotionConflict("promotion_decision_required") from exc
            intended = self._load_journal(connection, command_key, "intended")
            if intended is None:
                now = self._now()
                intended = _JournalEntry(
                    command_key=command_key,
                    candidate_id=candidate_id,
                    action="promote",
                    status="intended",
                    decision_request_id=command.decision_request_id,
                    request_hash=request_hash,
                    principal_id=auth.principal.id,
                    reason=command.reason,
                    pre_transition_candidate_version=candidate.version,
                    candidate_digest=candidate.candidate.artifact_digest,
                    base_digest=candidate.base.artifact_digest,
                    gate_snapshot_version=start.gate_snapshot_version,
                    gate_report_hash=start.gate_report_hash,
                    routing_version=candidate.routing.routing_version
                    if candidate.routing
                    else None,
                    allocation_basis_points=(
                        candidate.routing.allocation_basis_points if candidate.routing else None
                    ),
                )
                self._append_journal(
                    connection,
                    command_key=command_key,
                    candidate=candidate,
                    decision_request_id=command.decision_request_id,
                    entry=intended,
                    now=now,
                )
            else:
                self._require_command_binding(
                    intended,
                    candidate=candidate,
                    command_key=command_key,
                    action="promote",
                    command=command,
                    request_hash=request_hash,
                    auth=auth,
                )
            unit_of_work.commit()

        if applied_entry is None:
            try:
                effect = self._adapter_resolver(candidate.kind).activate(candidate)
            except Exception as exc:
                raise PromotionConflict("promotion_activation_failed") from exc
        else:
            effect = self._activation_effect(applied_entry, candidate=candidate)
        if (
            effect.candidate_id != candidate_id
            or effect.artifact_digest != candidate.candidate.artifact_digest
        ):
            raise PromotionConflict("promotion_activation_receipt_invalid")
        if applied_entry is None:
            with self._storage.unit_of_work() as unit_of_work:
                applied = intended.model_copy(
                    update={
                        "status": "applied",
                        "receipt": effect.model_dump(mode="json"),
                    }
                )
                self._append_journal(
                    unit_of_work.connection,
                    command_key=command_key,
                    candidate=candidate,
                    decision_request_id=command.decision_request_id,
                    entry=applied,
                    now=self._now(),
                )
                unit_of_work.commit()

        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            existing = self._completed_receipt(
                connection,
                command_key=command_key,
                request_hash=request_hash,
                receipt_type=PromotionReceiptV1,
                candidate=candidate,
                action="promote",
                command=command,
                auth=auth,
            )
            if existing is not None:
                unit_of_work.commit()
                return existing
            current = self._get_authorized_candidate(connection, candidate_id, auth=auth)
            self._require_expected_version(current, command.expected_version)
            if current.lifecycle is not CandidateLifecycle.CANARY:
                raise PromotionConflict("promotion_preconditions_not_met")
            start = self._require_start_binding(connection, current)
            self._validate_bound_report(connection, current, start)
            row = self._require_routing_matches(connection, current, start)
            now = self._now()
            routing = self._zero_routing(connection, current, row=row, now=now)
            try:
                durable = self._repository.save_candidate(
                    connection,
                    current.model_copy(
                        update={
                            "routing": routing,
                            "lifecycle": CandidateLifecycle.PROMOTED,
                            "updated_at": now,
                        }
                    ),
                    expected_version=current.version,
                    high_risk_decision_request_id=command.decision_request_id,
                )
            except EvolutionRepositoryConflict as exc:
                code = (
                    "promotion_decision_required"
                    if current.kind is CandidateKind.CODE
                    else "candidate_version_conflict"
                )
                raise PromotionConflict(code) from exc
            receipt = PromotionReceiptV1(
                action="promote",
                idempotency_key=command.idempotency_key,
                journal_id=_journal_id(command_key, "completed"),
                candidate_id=candidate_id,
                candidate_version=durable.version,
                gate_snapshot_version=start.gate_snapshot_version,
                gate_report_hash=start.gate_report_hash,
                lifecycle=durable.lifecycle,
                routing_version=routing.routing_version,
                allocation_basis_points=0,
                effect_artifact_digest=effect.artifact_digest,
                completed_at=now,
            )
            completed = intended.model_copy(
                update={
                    "status": "completed",
                    "routing_version": routing.routing_version,
                    "allocation_basis_points": 0,
                    "receipt": receipt.model_dump(mode="json"),
                }
            )
            self._append_journal(
                connection,
                command_key=command_key,
                candidate=current,
                decision_request_id=command.decision_request_id,
                entry=completed,
                now=now,
            )
            self._record(
                connection,
                candidate=durable,
                auth=auth,
                action="promote",
                routing=routing,
            )
            unit_of_work.commit()
            return receipt

    def rollback(
        self, candidate_id: str, command: RollbackCommand, *, auth: AuthContext
    ) -> RollbackReceiptV1:
        self._authorize(auth)
        request_hash = _request_hash(candidate_id, "rollback", command)
        command_key = _command_key(auth, command.idempotency_key)
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            authorized = self._get_authorized_candidate(connection, candidate_id, auth=auth)
            existing = self._completed_receipt(
                connection,
                command_key=command_key,
                request_hash=request_hash,
                receipt_type=RollbackReceiptV1,
                candidate=authorized,
                action="rollback",
                command=command,
                auth=auth,
            )
            if existing is not None:
                unit_of_work.commit()
                return existing
            applied_entry = self._load_journal(connection, command_key, "applied")
            if applied_entry is not None:
                self._require_command_binding(
                    applied_entry,
                    candidate=authorized,
                    command_key=command_key,
                    action="rollback",
                    command=command,
                    request_hash=request_hash,
                    auth=auth,
                )
            pending_entry = self._load_journal(connection, command_key, "rollback_pending")
            if pending_entry is None:
                current = authorized
                self._require_expected_version(current, command.expected_version)
                if current.lifecycle not in {
                    CandidateLifecycle.CANARY,
                    CandidateLifecycle.PROMOTED,
                }:
                    raise PromotionConflict("promotion_preconditions_not_met")
                row = self._require_routing_matches(connection, current)
                now = self._now()
                routing = self._zero_routing(connection, current, row=row, now=now)
                pending = self._repository.save_candidate(
                    connection,
                    current.model_copy(
                        update={
                            "routing": routing,
                            "lifecycle": CandidateLifecycle.ROLLBACK_PENDING,
                            "updated_at": now,
                        }
                    ),
                    expected_version=current.version,
                )
                pending_entry = _JournalEntry(
                    command_key=command_key,
                    candidate_id=candidate_id,
                    action="rollback",
                    status="rollback_pending",
                    decision_request_id=command.decision_request_id,
                    request_hash=request_hash,
                    principal_id=auth.principal.id,
                    reason=command.reason,
                    pre_transition_candidate_version=current.version,
                    candidate_digest=current.candidate.artifact_digest,
                    base_digest=current.base.artifact_digest,
                    gate_snapshot_version=current.gate_snapshot_version,
                    gate_report_hash=self._gate_hash_for_candidate(connection, current),
                    routing_version=routing.routing_version,
                    allocation_basis_points=0,
                )
                self._append_journal(
                    connection,
                    command_key=command_key,
                    candidate=current,
                    decision_request_id=command.decision_request_id,
                    entry=pending_entry,
                    now=now,
                )
                self._record(
                    connection,
                    candidate=pending,
                    auth=auth,
                    action="rollback",
                    routing=routing,
                    pending=True,
                )
            else:
                self._require_command_binding(
                    pending_entry,
                    candidate=authorized,
                    command_key=command_key,
                    action="rollback",
                    command=command,
                    request_hash=request_hash,
                    auth=auth,
                )
                pending = self._repository.get_candidate(connection, candidate_id)
                if (
                    pending is None
                    or pending.lifecycle is not CandidateLifecycle.ROLLBACK_PENDING
                    or pending.version != pending_entry.pre_transition_candidate_version + 1
                    or pending.routing is None
                    or pending.routing.allocation_basis_points != 0
                    or pending.routing.routing_version != pending_entry.routing_version
                ):
                    raise PromotionConflict("candidate_version_conflict")
            unit_of_work.commit()

        if applied_entry is None:
            try:
                effect = self._adapter_resolver(pending.kind).rollback(pending)
            except Exception as exc:
                raise PromotionConflict("rollback_restore_failed") from exc
        else:
            effect = self._rollback_effect(applied_entry, candidate=pending)
        if (
            effect.candidate_id != candidate_id
            or effect.artifact_digest != pending.base.artifact_digest
        ):
            raise PromotionConflict("rollback_restore_receipt_invalid")
        if applied_entry is None:
            with self._storage.unit_of_work() as unit_of_work:
                applied = pending_entry.model_copy(
                    update={
                        "status": "applied",
                        "receipt": effect.model_dump(mode="json"),
                    }
                )
                self._append_journal(
                    unit_of_work.connection,
                    command_key=command_key,
                    candidate=pending,
                    decision_request_id=command.decision_request_id,
                    entry=applied,
                    now=self._now(),
                )
                unit_of_work.commit()

        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            existing = self._completed_receipt(
                connection,
                command_key=command_key,
                request_hash=request_hash,
                receipt_type=RollbackReceiptV1,
                candidate=pending,
                action="rollback",
                command=command,
                auth=auth,
            )
            if existing is not None:
                unit_of_work.commit()
                return existing
            current = self._repository.get_candidate(connection, candidate_id)
            if current is None:
                raise PromotionConflict("candidate_version_conflict")
            self._authorize_candidate(auth, current)
            if (
                current.lifecycle is not CandidateLifecycle.ROLLBACK_PENDING
                or current.version != pending_entry.pre_transition_candidate_version + 1
                or current.routing is None
                or current.routing.allocation_basis_points != 0
            ):
                raise PromotionConflict("candidate_version_conflict")
            now = self._now()
            durable = self._repository.save_candidate(
                connection,
                current.model_copy(
                    update={"lifecycle": CandidateLifecycle.ROLLED_BACK, "updated_at": now}
                ),
                expected_version=current.version,
            )
            receipt = RollbackReceiptV1(
                idempotency_key=command.idempotency_key,
                journal_id=_journal_id(command_key, "completed"),
                candidate_id=candidate_id,
                candidate_version=durable.version,
                lifecycle=CandidateLifecycle.ROLLED_BACK,
                routing_version=current.routing.routing_version,
                effect_artifact_digest=effect.artifact_digest,
                completed_at=now,
            )
            completed = pending_entry.model_copy(
                update={"status": "completed", "receipt": receipt.model_dump(mode="json")}
            )
            self._append_journal(
                connection,
                command_key=command_key,
                candidate=current,
                decision_request_id=command.decision_request_id,
                entry=completed,
                now=now,
            )
            self._record(
                connection,
                candidate=durable,
                auth=auth,
                action="rollback",
                routing=current.routing,
            )
            unit_of_work.commit()
            return receipt

    @staticmethod
    def _authorize(auth: AuthContext) -> None:
        if (
            not auth.principal.id.strip()
            or not auth.principal.display_name.strip()
            or auth.principal.scopes.isdisjoint({"api", "admin"})
        ):
            raise PromotionAuthorizationError("promotion_scope_required")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("promotion clock must be timezone-aware")
        return value.astimezone(UTC)

    def _get_authorized_candidate(
        self, connection: sqlite3.Connection, candidate_id: str, *, auth: AuthContext
    ) -> EvolutionCandidateV1:
        candidate = self._repository.get_candidate(connection, candidate_id)
        if candidate is None:
            raise PromotionConflict("candidate_not_found")
        self._authorize_candidate(auth, candidate)
        return candidate

    @staticmethod
    def _authorize_candidate(auth: AuthContext, candidate: EvolutionCandidateV1) -> None:
        if (
            "admin" not in auth.principal.scopes
            and auth.principal.id != candidate.provenance.actor_principal_id
        ):
            raise PromotionAuthorizationError("promotion_candidate_owner_required")

    @staticmethod
    def _require_expected_version(candidate: EvolutionCandidateV1, expected_version: int) -> None:
        if candidate.version != expected_version:
            raise PromotionConflict("candidate_version_conflict")

    @staticmethod
    def _routing_row(connection: sqlite3.Connection, candidate_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM evolution_routing_allocations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()

    def _require_routing_matches(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        start: _JournalEntry | None = None,
    ) -> sqlite3.Row:
        row = self._routing_row(connection, candidate.candidate_id)
        routing = candidate.routing
        if row is None or routing is None:
            raise PromotionConflict("routing_allocation_conflict")
        payload = routing.model_dump(mode="json")
        if (
            row["routing_version"] != routing.routing_version
            or row["allocation_basis_points"] != routing.allocation_basis_points
            or row["allocation_seed_id"] != routing.allocation_seed_id
            or row["routing_json"] != canonical_json_bytes(payload).decode("utf-8")
            or row["routing_hash"] != canonical_sha256(payload)
            or (start is not None and start.routing_version != routing.routing_version)
            or (
                start is not None
                and start.allocation_basis_points != routing.allocation_basis_points
            )
        ):
            raise PromotionConflict("routing_allocation_conflict")
        return row

    @staticmethod
    def _insert_routing(
        connection: sqlite3.Connection,
        candidate_id: str,
        routing: RoutingPolicyV1,
        *,
        now: datetime,
    ) -> None:
        payload = routing.model_dump(mode="json")
        connection.execute(
            """INSERT INTO evolution_routing_allocations (
                   candidate_id, routing_version, allocation_basis_points,
                   allocation_seed_id, routing_json, routing_hash, version,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                candidate_id,
                routing.routing_version,
                routing.allocation_basis_points,
                routing.allocation_seed_id,
                canonical_json_bytes(payload).decode("utf-8"),
                canonical_sha256(payload),
                now.isoformat(),
                now.isoformat(),
            ),
        )

    @staticmethod
    def _zero_routing(
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        *,
        row: sqlite3.Row,
        now: datetime,
    ) -> RoutingPolicyV1:
        assert candidate.routing is not None
        routing = candidate.routing.model_copy(
            update={
                "allocation_basis_points": 0,
                "routing_version": candidate.routing.routing_version + 1,
            }
        )
        payload = routing.model_dump(mode="json")
        cursor = connection.execute(
            """UPDATE evolution_routing_allocations
               SET routing_version=?, allocation_basis_points=0,
                   routing_json=?, routing_hash=?, version=version+1, updated_at=?
               WHERE candidate_id=? AND version=? AND routing_version=?""",
            (
                routing.routing_version,
                canonical_json_bytes(payload).decode("utf-8"),
                canonical_sha256(payload),
                now.isoformat(),
                candidate.candidate_id,
                row["version"],
                candidate.routing.routing_version,
            ),
        )
        if cursor.rowcount != 1:
            raise PromotionConflict("routing_allocation_conflict")
        return routing

    def _require_start_binding(
        self, connection: sqlite3.Connection, candidate: EvolutionCandidateV1
    ) -> _JournalEntry:
        rows = connection.execute(
            f"""SELECT {_JOURNAL_COLUMNS} FROM evolution_promotion_journal
                WHERE candidate_id=? AND action='start_canary' AND status='completed'""",
            (candidate.candidate_id,),
        ).fetchall()
        if len(rows) != 1:
            raise PromotionConflict("gate_snapshot_conflict")
        entry = self._decode_entry(rows[0])
        if (
            entry.action != "start_canary"
            or entry.status != "completed"
            or entry.candidate_id != candidate.candidate_id
            or entry.pre_transition_candidate_version + 1 != candidate.version
            or entry.candidate_digest != candidate.candidate.artifact_digest
            or entry.base_digest != candidate.base.artifact_digest
            or entry.gate_snapshot_version != candidate.gate_snapshot_version
        ):
            raise PromotionConflict("gate_snapshot_conflict")
        return entry

    def _validate_bound_report(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        binding: _JournalEntry,
    ) -> None:
        try:
            self._gates.validate_bound_green_report_current(
                connection,
                candidate.candidate_id,
                candidate_version=binding.pre_transition_candidate_version,
                gate_snapshot_version=binding.gate_snapshot_version,
                candidate_digest=binding.candidate_digest,
                report_hash=binding.gate_report_hash,
            )
        except EvolutionRepositoryConflict as exc:
            raise PromotionConflict("gate_snapshot_conflict") from exc

    def _gate_hash_for_candidate(
        self, connection: sqlite3.Connection, candidate: EvolutionCandidateV1
    ) -> str:
        try:
            start = self._require_start_binding(connection, candidate)
            return start.gate_report_hash
        except PromotionConflict:
            row = connection.execute(
                """SELECT snapshot_hash FROM evolution_gate_snapshots
                   WHERE candidate_id=? AND gate_snapshot_version=?""",
                (candidate.candidate_id, candidate.gate_snapshot_version),
            ).fetchone()
            if row is None or not isinstance(row["snapshot_hash"], str):
                raise PromotionConflict("gate_snapshot_conflict") from None
            return str(row["snapshot_hash"])

    @staticmethod
    def _append_journal(
        connection: sqlite3.Connection,
        *,
        command_key: str,
        candidate: EvolutionCandidateV1,
        decision_request_id: str | None,
        entry: _JournalEntry,
        now: datetime,
    ) -> None:
        if (
            entry.command_key != command_key
            or entry.candidate_id != candidate.candidate_id
            or entry.decision_request_id != decision_request_id
        ):
            raise PromotionConflict("promotion_journal_conflict")
        entry_json = canonical_json_bytes(entry).decode("utf-8")
        try:
            connection.execute(
                """INSERT INTO evolution_promotion_journal (
                       promotion_journal_id, command_key, candidate_id,
                       candidate_version, gate_snapshot_version, action, status,
                       decision_request_id, entry_json, entry_hash, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _journal_id(entry.command_key, entry.status),
                    entry.command_key,
                    entry.candidate_id,
                    entry.pre_transition_candidate_version,
                    entry.gate_snapshot_version,
                    entry.action,
                    entry.status,
                    entry.decision_request_id,
                    entry_json,
                    hashlib.sha256(entry_json.encode()).hexdigest(),
                    now.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PromotionConflict("promotion_journal_conflict") from exc

    def _load_journal(
        self, connection: sqlite3.Connection, command_key: str, status: str
    ) -> _JournalEntry | None:
        row = connection.execute(
            f"""SELECT {_JOURNAL_COLUMNS} FROM evolution_promotion_journal
                WHERE command_key=? AND status=?""",
            (command_key, status),
        ).fetchone()
        return self._decode_entry(row) if row is not None else None

    @staticmethod
    def _decode_entry(row: sqlite3.Row) -> _JournalEntry:
        raw = row["entry_json"]
        if (
            not isinstance(raw, str)
            or hashlib.sha256(raw.encode()).hexdigest() != row["entry_hash"]
        ):
            raise PromotionConflict("promotion_journal_conflict")
        try:
            entry = _JournalEntry.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise PromotionConflict("promotion_journal_conflict") from exc
        if canonical_json_bytes(entry).decode("utf-8") != raw:
            raise PromotionConflict("promotion_journal_conflict")
        valid_statuses = {
            "start_canary": {"completed"},
            "promote": {"intended", "applied", "completed"},
            "rollback": {"rollback_pending", "applied", "completed"},
        }
        if (
            row["promotion_journal_id"] != _journal_id(entry.command_key, entry.status)
            or row["command_key"] != entry.command_key
            or row["candidate_id"] != entry.candidate_id
            or row["candidate_version"] != entry.pre_transition_candidate_version
            or row["gate_snapshot_version"] != entry.gate_snapshot_version
            or row["action"] != entry.action
            or row["status"] != entry.status
            or row["decision_request_id"] != entry.decision_request_id
            or entry.status not in valid_statuses[entry.action]
            or ((entry.status in {"applied", "completed"}) != (entry.receipt is not None))
        ):
            raise PromotionConflict("promotion_journal_conflict")
        return entry

    @staticmethod
    def _require_same_request(entry: _JournalEntry, request_hash: str) -> None:
        if entry.request_hash != request_hash:
            raise PromotionConflict("idempotency_conflict")

    @classmethod
    def _require_command_binding(
        cls,
        entry: _JournalEntry,
        *,
        candidate: EvolutionCandidateV1,
        command_key: str,
        action: Literal["start_canary", "promote", "rollback"],
        command: StartCanaryCommand | PromoteCommand | RollbackCommand,
        request_hash: str,
        auth: AuthContext,
    ) -> None:
        cls._require_same_request(entry, request_hash)
        if (
            entry.command_key != command_key
            or entry.candidate_id != candidate.candidate_id
            or entry.action != action
            or entry.decision_request_id != command.decision_request_id
            or entry.pre_transition_candidate_version != command.expected_version
            or entry.candidate_digest != candidate.candidate.artifact_digest
            or entry.base_digest != candidate.base.artifact_digest
            or entry.principal_id != auth.principal.id
            or entry.reason != command.reason
        ):
            raise PromotionConflict("promotion_journal_conflict")

    def _completed_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        command_key: str,
        request_hash: str,
        receipt_type: type[PromotionReceiptV1] | type[RollbackReceiptV1],
        candidate: EvolutionCandidateV1,
        action: Literal["start_canary", "promote", "rollback"],
        command: StartCanaryCommand | PromoteCommand | RollbackCommand,
        auth: AuthContext,
    ) -> PromotionReceiptV1 | RollbackReceiptV1 | None:
        entry = self._load_journal(connection, command_key, "completed")
        if entry is None:
            return None
        self._require_command_binding(
            entry,
            candidate=candidate,
            command_key=command_key,
            action=action,
            command=command,
            request_hash=request_hash,
            auth=auth,
        )
        if entry.status != "completed" or entry.receipt is None:
            raise PromotionConflict("promotion_journal_conflict")
        try:
            receipt = receipt_type.model_validate_json(json.dumps(entry.receipt))
        except ValidationError as exc:
            raise PromotionConflict("promotion_journal_conflict") from exc
        common_invalid = (
            receipt.journal_id != _journal_id(command_key, "completed")
            or receipt.candidate_id != candidate.candidate_id
            or receipt.idempotency_key != command.idempotency_key
        )
        if isinstance(receipt, PromotionReceiptV1):
            effect_digest = candidate.candidate.artifact_digest if action == "promote" else None
            invalid = (
                common_invalid
                or receipt.action != action
                or receipt.candidate_version != command.expected_version + 1
                or receipt.gate_snapshot_version != entry.gate_snapshot_version
                or receipt.gate_report_hash != entry.gate_report_hash
                or receipt.routing_version != entry.routing_version
                or receipt.allocation_basis_points != entry.allocation_basis_points
                or receipt.effect_artifact_digest != effect_digest
                or (action == "start_canary" and receipt.lifecycle is not CandidateLifecycle.CANARY)
                or (action == "promote" and receipt.lifecycle is not CandidateLifecycle.PROMOTED)
            )
        else:
            invalid = (
                common_invalid
                or action != "rollback"
                or receipt.candidate_version != command.expected_version + 2
                or receipt.lifecycle is not CandidateLifecycle.ROLLED_BACK
                or receipt.routing_version != entry.routing_version
                or receipt.allocation_basis_points != 0
                or receipt.effect_artifact_digest != entry.base_digest
            )
        if invalid:
            raise PromotionConflict("promotion_journal_conflict")
        return receipt

    @staticmethod
    def _activation_effect(
        entry: _JournalEntry, *, candidate: EvolutionCandidateV1
    ) -> ActivationReceiptV1:
        if entry.receipt is None:
            raise PromotionConflict("promotion_journal_conflict")
        try:
            receipt = ActivationReceiptV1.model_validate_json(json.dumps(entry.receipt))
        except ValidationError as exc:
            raise PromotionConflict("promotion_journal_conflict") from exc
        if (
            entry.action != "promote"
            or entry.status != "applied"
            or entry.candidate_id != candidate.candidate_id
            or receipt.candidate_id != entry.candidate_id
            or receipt.artifact_digest != entry.candidate_digest
        ):
            raise PromotionConflict("promotion_journal_conflict")
        return receipt

    @staticmethod
    def _rollback_effect(
        entry: _JournalEntry, *, candidate: EvolutionCandidateV1
    ) -> AdapterRollbackReceiptV1:
        if entry.receipt is None:
            raise PromotionConflict("promotion_journal_conflict")
        try:
            receipt = AdapterRollbackReceiptV1.model_validate_json(json.dumps(entry.receipt))
        except ValidationError as exc:
            raise PromotionConflict("promotion_journal_conflict") from exc
        if (
            entry.action != "rollback"
            or entry.status != "applied"
            or entry.candidate_id != candidate.candidate_id
            or receipt.candidate_id != entry.candidate_id
            or receipt.artifact_digest != entry.base_digest
        ):
            raise PromotionConflict("promotion_journal_conflict")
        return receipt

    def _record(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: EvolutionCandidateV1,
        auth: AuthContext,
        action: Literal["start_canary", "promote", "rollback"],
        routing: RoutingPolicyV1,
        pending: bool = False,
    ) -> None:
        event_action = f"evolution.promotion.{action}"
        _append_system_audit_unlocked(
            connection,
            AppendSystemAuditRequest(
                correlation_id=auth.correlation_id,
                actor_digest=hashlib.sha256(auth.principal.id.encode()).hexdigest(),
                action=event_action,
                outcome="succeeded",
                reason_code="rollback_pending" if pending else "promotion_persisted",
                subject_kind="evolution_candidate",
                subject_digest=hashlib.sha256(candidate.candidate_id.encode()).hexdigest(),
                metadata={},
            ),
        )
        self._outbox.add(
            connection,
            make_event(
                event_type=event_action,
                producer="promotion_service",
                payload={
                    "candidate_id": candidate.candidate_id,
                    "candidate_version": candidate.version,
                    "lifecycle": candidate.lifecycle.value,
                    "routing_version": routing.routing_version,
                    "allocation_basis_points": routing.allocation_basis_points,
                    "correlation_id": auth.correlation_id,
                },
            ),
        )


__all__ = [
    "PromoteCommand",
    "PromotionAuthorizationError",
    "PromotionConflict",
    "PromotionError",
    "PromotionReceiptV1",
    "PromotionService",
    "RollbackCommand",
    "RollbackReceiptV1",
    "SkillPromotionAdapter",
    "StartCanaryCommand",
    "UnavailablePromotionAdapter",
]
