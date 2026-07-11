"""Immutable G1 workspace governance records."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MODES = frozenset({"100644", "100755", "120000"})


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_oid(value: str | None) -> str | None:
    if value is not None and not _OID_RE.fullmatch(value):
        raise ValueError("Git object id must be a lowercase SHA-1 or SHA-256")
    return value


def _validate_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be 64 lowercase hexadecimal characters")
    return value


def _validate_optional_digest(value: str | None) -> str | None:
    return _validate_digest(value) if value is not None else None


def _validate_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in path.parts)
    ):
        raise ValueError("change path must be a safe relative Git path")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1"] = "1"


class WorkspaceLeaseState(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    CLOSING = "closing"
    CLEANUP_FAILED = "cleanup_failed"
    CLOSED = "closed"


class WorkspaceLease(WorkspaceRecord):
    id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=256)
    lineage_root_run_id: str = Field(min_length=1, max_length=256)
    parent_run_id: str | None = Field(default=None, max_length=256)
    attempt: int = Field(ge=0)
    source_kind: Literal["git", "scratch"]
    apply_mode: Literal["governed", "none"]
    source_root: str | None = None
    source_repository_id: str | None = None
    source_git_dir: str | None = None
    source_git_dir_identity: str | None = None
    base_revision: str | None = None
    staging_root: str = Field(min_length=1)
    staging_git_dir: str | None = None
    staging_git_dir_identity: str | None = None
    state: WorkspaceLeaseState
    state_version: int = Field(ge=1)
    created_at: datetime

    _normalize_created_at = field_validator("created_at")(_utc)
    _validate_base = field_validator("base_revision")(_validate_oid)
    _validate_git_dir_digests = field_validator(
        "source_git_dir_identity", "staging_git_dir_identity"
    )(_validate_optional_digest)

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        source_fields = (
            self.source_root,
            self.source_repository_id,
            self.source_git_dir,
            self.source_git_dir_identity,
            self.base_revision,
        )
        staging_fields = (self.staging_git_dir, self.staging_git_dir_identity)
        if self.source_kind == "git":
            if self.apply_mode != "governed" or any(value is None for value in source_fields):
                raise ValueError("Git leases require governed mode, source identity, and base")
            if any(value is None for value in staging_fields) and not all(
                value is None for value in staging_fields
            ):
                raise ValueError("staging Git authority must be complete")
            if self.state in {
                WorkspaceLeaseState.ACTIVE,
                WorkspaceLeaseState.CLOSING,
            } and any(value is None for value in staging_fields):
                raise ValueError("active or closing Git leases require staging authority")
        elif self.apply_mode != "none" or any(
            value is not None for value in (*source_fields, *staging_fields)
        ):
            raise ValueError("scratch leases require apply_mode=none and no Git source")
        if self.attempt == 0:
            if self.parent_run_id is not None or self.lineage_root_run_id != self.run_id:
                raise ValueError("root workspace runs require no parent and self lineage")
        elif self.parent_run_id is None or self.parent_run_id == self.run_id:
            raise ValueError("retry workspace runs require a distinct parent")
        return self


class WorkspaceStagingIdentity(WorkspaceRecord):
    lease_id: str = Field(min_length=1, max_length=128)
    staging_root: str = Field(min_length=1)
    git_dir: str = Field(min_length=1)
    git_dir_identity: str
    source_repository_id: str = Field(min_length=1)
    base_revision: str
    created_at: datetime

    _validate_git_dir_identity = field_validator("git_dir_identity")(_validate_digest)
    _validate_base = field_validator("base_revision")(_validate_oid)
    _normalize_created_at = field_validator("created_at")(_utc)


class RestorePoint(WorkspaceRecord):
    id: str = Field(min_length=1, max_length=128)
    lease_id: str = Field(min_length=1, max_length=128)
    source_repository_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    source_git_dir: str = Field(min_length=1)
    source_git_dir_identity: str
    base_revision: str
    source_head_revision: str
    source_head_ref: str | None = None
    source_index_tree: str
    source_status_hash: str
    created_at: datetime

    _validate_oids = field_validator("base_revision", "source_head_revision", "source_index_tree")(
        _validate_oid
    )
    _validate_status_hash = field_validator("source_status_hash")(_validate_digest)
    _validate_git_dir_identity = field_validator("source_git_dir_identity")(_validate_digest)
    _normalize_created_at = field_validator("created_at")(_utc)

    def canonical_json(self) -> str:
        return _canonical_json(
            self.model_dump(
                mode="json",
                exclude={"id", "created_at"},
            )
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class CanonicalChange(WorkspaceRecord):
    kind: Literal["add", "modify", "delete", "rename", "copy", "mode", "untracked"]
    old_path: str | None = None
    new_path: str | None = None
    old_oid: str | None = None
    new_oid: str | None = None
    old_mode: str | None = None
    new_mode: str | None = None
    old_size: int | None = Field(default=None, ge=0)
    new_size: int | None = Field(default=None, ge=0)
    binary: bool

    _validate_paths = field_validator("old_path", "new_path")(_validate_path)
    _validate_oids = field_validator("old_oid", "new_oid")(_validate_oid)

    @field_validator("old_mode", "new_mode")
    @classmethod
    def validate_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in _MODES:
            raise ValueError("unsupported Git file mode")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        old_values = (self.old_path, self.old_oid, self.old_mode, self.old_size)
        new_values = (self.new_path, self.new_oid, self.new_mode, self.new_size)
        has_old = all(value is not None for value in old_values)
        has_new = all(value is not None for value in new_values)
        expected = {
            "add": (False, True),
            "untracked": (False, True),
            "delete": (True, False),
            "modify": (True, True),
            "mode": (True, True),
            "rename": (True, True),
            "copy": (True, True),
        }[self.kind]
        if (has_old, has_new) != expected:
            raise ValueError(f"{self.kind} change has incomplete old/new identity")
        if not expected[0] and any(value is not None for value in old_values):
            raise ValueError(f"{self.kind} change must not carry old identity")
        if not expected[1] and any(value is not None for value in new_values):
            raise ValueError(f"{self.kind} change must not carry new identity")
        if self.kind in {"modify", "mode"} and self.old_path != self.new_path:
            raise ValueError(f"{self.kind} must keep the same path")
        if self.kind in {"rename", "copy"} and self.old_path == self.new_path:
            raise ValueError(f"{self.kind} must change the path")
        if self.kind == "mode" and (self.old_oid != self.new_oid or self.old_mode == self.new_mode):
            raise ValueError("mode changes keep content and change mode")
        if self.kind == "modify" and self.old_oid == self.new_oid:
            raise ValueError("modify changes must change content")
        if self.kind in {"rename", "copy"} and self.old_oid != self.new_oid:
            raise ValueError(f"{self.kind} changes require exact content identity")
        if (
            self.old_oid is not None
            and self.old_oid == self.new_oid
            and self.old_size != self.new_size
        ):
            raise ValueError("the same Git object id must have the same size")
        return self

    def sort_key(self) -> tuple[bytes, bytes, str]:
        return (
            os.fsencode(self.new_path or ""),
            os.fsencode(self.old_path or ""),
            self.kind,
        )


class CanonicalChangeSet(WorkspaceRecord):
    id: str = Field(min_length=1, max_length=128)
    lease_id: str = Field(min_length=1, max_length=128)
    restore_point_id: str = Field(min_length=1, max_length=128)
    source_repository_id: str = Field(min_length=1)
    base_revision: str
    sequence: int = Field(ge=1)
    changes: tuple[CanonicalChange, ...]
    created_at: datetime

    _validate_base = field_validator("base_revision")(_validate_oid)
    _normalize_created_at = field_validator("created_at")(_utc)

    @field_validator("changes", mode="after")
    @classmethod
    def canonical_order(cls, values: tuple[CanonicalChange, ...]) -> tuple[CanonicalChange, ...]:
        ordered = tuple(sorted(values, key=CanonicalChange.sort_key))
        keys = [item.sort_key() for item in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("canonical changes contain duplicate path identities")
        target_paths = [item.new_path for item in ordered if item.new_path is not None]
        if len(target_paths) != len(set(target_paths)):
            raise ValueError("canonical changes contain duplicate target paths")
        return ordered

    def canonical_json(self) -> str:
        return _canonical_json(
            self.model_dump(
                mode="json",
                exclude={"id", "sequence", "created_at"},
            )
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class ApplyDecision(WorkspaceRecord):
    id: str = Field(min_length=1, max_length=128)
    lease_id: str = Field(min_length=1, max_length=128)
    restore_point_id: str = Field(min_length=1, max_length=128)
    change_set_id: str = Field(min_length=1, max_length=128)
    change_set_hash: str
    source_repository_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    base_revision: str
    source_head_ref: str | None = None
    principal_digest: str
    apply_scope: Literal["workspace"] = "workspace"
    reason: str = Field(min_length=1)
    decision_hash: str
    token_hash: str
    state: Literal["pending", "consumed", "expired", "revoked"] = "pending"
    state_version: int = Field(default=1, ge=1)
    expires_at: datetime
    created_at: datetime

    _validate_digests = field_validator(
        "change_set_hash", "principal_digest", "decision_hash", "token_hash"
    )(_validate_digest)
    _validate_base = field_validator("base_revision")(_validate_oid)
    _normalize_timestamps = field_validator("expires_at", "created_at")(_utc)


class ApplyReceipt(WorkspaceRecord):
    id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    decision_hash: str
    lease_id: str = Field(min_length=1, max_length=128)
    change_set_id: str = Field(min_length=1, max_length=128)
    change_set_hash: str
    outcome: Literal["succeeded", "failed", "denied"]
    detail: str
    pre_source_head: str
    pre_source_status_hash: str
    post_source_head: str
    post_source_status_hash: str
    rollback_status: Literal["not_required", "not_attempted", "succeeded", "failed"]
    failure_code: str | None = None
    evidence: tuple[str, ...] = ()
    created_at: datetime

    _validate_digests = field_validator(
        "decision_hash",
        "change_set_hash",
        "pre_source_status_hash",
        "post_source_status_hash",
    )(_validate_digest)
    _validate_heads = field_validator("pre_source_head", "post_source_head")(_validate_oid)
    _normalize_created_at = field_validator("created_at")(_utc)


__all__ = [
    "ApplyDecision",
    "ApplyReceipt",
    "CanonicalChange",
    "CanonicalChangeSet",
    "RestorePoint",
    "WorkspaceLease",
    "WorkspaceLeaseState",
    "WorkspaceStagingIdentity",
]
