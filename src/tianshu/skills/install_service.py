"""Authenticated command contracts for governed skill proposals."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import JsonValue
from tianshu.models.events import make_event
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateProposalV1,
    CandidateSourceChannel,
    CandidateSourceV1,
    EvolutionCandidateV1,
    EvolutionContractV1,
    ProvenanceInputV1,
)
from tianshu.models.principal import AuthContext, ClientKind
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.skills.installer import (
    canonical_skill_package_member_path,
)
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked


class SkillInstallServiceError(RuntimeError):
    """Base failure for governed skill proposals."""


class SkillInstallAuthorizationError(SkillInstallServiceError):
    """The authenticated principal cannot perform the requested skill write."""


class StagedCandidateV1(Protocol):
    candidate_id: str
    lifecycle: CandidateLifecycle


class _CandidateAuthority(Protocol):
    def propose(
        self,
        proposal: CandidateProposalV1,
        *,
        on_persist: Callable[[sqlite3.Connection, EvolutionCandidateV1], None] | None = None,
    ) -> EvolutionCandidateV1: ...

    def stage(
        self,
        candidate_id: str,
        *,
        on_persist: Callable[[sqlite3.Connection, EvolutionCandidateV1], None] | None = None,
    ) -> object: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ProposedSkillMemberV1(_StrictModel):
    path: str
    kind: Literal["file", "directory", "symlink_file", "symlink_directory"]
    content: str | None = None


class ProposeSkillCommand(_StrictModel):
    schema_version: Literal[1] = 1
    command_id: str
    name: str
    version: str
    base_version: str
    base_state: Literal["present", "absent"] = "present"
    source_channel: CandidateSourceChannel
    base_members: tuple[ProposedSkillMemberV1, ...]
    members: tuple[ProposedSkillMemberV1, ...] = Field(min_length=1)
    evidence_bundle_ids: tuple[str, ...]
    restore_point_ref: str

    @field_validator("command_id", "name", "version", "base_version", "restore_point_ref")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("skill proposal identity must not be blank")
        return value

    @field_validator("source_channel", mode="before")
    @classmethod
    def parse_source_channel(cls, value: object) -> object:
        return CandidateSourceChannel(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_member_paths(self) -> Self:
        if self.base_state == "absent" and self.base_members:
            raise ValueError("absent skill base must not contain package members")
        if self.base_state == "present" and not self.base_members:
            raise ValueError("present skill base requires package members")
        for package in (self.base_members, self.members):
            paths: set[str] = set()
            for member in package:
                canonical = canonical_skill_package_member_path(member.path)
                if canonical != member.path or canonical in paths:
                    raise ValueError("skill package member paths must be unique and canonical")
                paths.add(canonical)
        return self


class SkillInstallService:
    def __init__(
        self,
        candidates: _CandidateAuthority,
        storage: object,
        *,
        contract_factory: Callable[[str], EvolutionContractV1],
    ) -> None:
        self._candidates = candidates
        self._storage = storage
        self._contract_factory = contract_factory
        self._repository = EvolutionRepository()
        self._outbox = OutboxRepository()

    def propose(self, command: ProposeSkillCommand, *, auth: AuthContext) -> EvolutionCandidateV1:
        self._authorize(command, auth)
        contract = self._contract_factory(command.name)
        if contract.kind is not CandidateKind.SKILL or contract.subject_key != (
            f"skill:{command.name}"
        ):
            raise ValueError("skill evolution contract is not bound to the command")
        proposal = CandidateProposalV1(
            command_id=command.command_id,
            kind=CandidateKind.SKILL,
            subject_key=contract.subject_key,
            base=CandidateSourceV1(
                version=command.base_version,
                payload=self._package_payload(
                    command.name,
                    command.base_members,
                    state=command.base_state,
                ),
            ),
            candidate=CandidateSourceV1(
                version=command.version,
                payload=self._package_payload(command.name, command.members, state="present"),
            ),
            evolution_contract=contract,
            provenance=ProvenanceInputV1(
                source_channel=command.source_channel,
                source_uri_redacted=None,
                actor_principal_id=auth.principal.id,
                actor_display_name=auth.principal.display_name,
                originating_edict_id=None,
                originating_memorial_id=None,
                producer_name="skill-install-service",
                producer_version="1",
            ),
            evidence_bundle_ids=command.evidence_bundle_ids,
            restore_point_ref=command.restore_point_ref,
        )
        return self._candidates.propose(
            proposal,
            on_persist=lambda connection, candidate: self._record(
                connection,
                candidate,
                auth=auth,
                action="skill.candidate.proposed",
            ),
        )

    def stage(self, candidate_id: str, *, auth: AuthContext) -> StagedCandidateV1:
        self._require_scope(auth)
        with self._storage.unit_of_work() as unit_of_work:  # type: ignore[attr-defined]
            candidate = self._repository.get_candidate(unit_of_work.connection, candidate_id)
            unit_of_work.commit()
        if candidate is None or candidate.kind is not CandidateKind.SKILL:
            raise ValueError("skill candidate was not found")
        if (
            candidate.provenance.actor_principal_id != auth.principal.id
            and "admin" not in auth.principal.scopes
        ):
            raise SkillInstallAuthorizationError("candidate staging principal mismatch")
        return cast(
            StagedCandidateV1,
            self._candidates.stage(
                candidate_id,
                on_persist=lambda connection, durable: self._record(
                    connection,
                    durable,
                    auth=auth,
                    action="skill.candidate.staged",
                ),
            ),
        )

    @staticmethod
    def _package_payload(
        name: str,
        members: tuple[ProposedSkillMemberV1, ...],
        *,
        state: Literal["present", "absent"],
    ) -> dict[str, JsonValue]:
        serialized_members: list[JsonValue] = [
            {"path": item.path, "kind": item.kind, "content": item.content} for item in members
        ]
        return {
            "name": name,
            "state": state,
            "trust_source": "community",
            "members": serialized_members,
        }

    @classmethod
    def _authorize(cls, command: ProposeSkillCommand, auth: AuthContext) -> None:
        cls._require_scope(auth)
        expected = {
            ClientKind.API: CandidateSourceChannel.API,
            ClientKind.CLI: CandidateSourceChannel.CLI,
            ClientKind.WEB: CandidateSourceChannel.API,
            ClientKind.MCP: CandidateSourceChannel.AGENT,
            ClientKind.WEBHOOK: CandidateSourceChannel.API,
            ClientKind.SYSTEM: CandidateSourceChannel.SYSTEM,
        }[auth.client_kind]
        if command.source_channel is not expected:
            raise SkillInstallAuthorizationError("skill source channel does not match auth")

    @staticmethod
    def _require_scope(auth: AuthContext) -> None:
        if not auth.principal.id.strip() or not auth.principal.display_name.strip():
            raise SkillInstallAuthorizationError("authenticated principal is incomplete")
        if auth.principal.scopes.isdisjoint({"api", "admin"}):
            raise SkillInstallAuthorizationError("skill write scope is required")

    def _record(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        *,
        auth: AuthContext,
        action: str,
    ) -> None:
        actor_digest = hashlib.sha256(auth.principal.id.encode()).hexdigest()
        subject_digest = hashlib.sha256(candidate.candidate_id.encode()).hexdigest()
        _append_system_audit_unlocked(
            connection,
            AppendSystemAuditRequest(
                correlation_id=auth.correlation_id,
                actor_digest=actor_digest,
                action=action,
                outcome="succeeded",
                reason_code="candidate_persisted",
                subject_kind="skill_candidate",
                subject_digest=subject_digest,
                metadata={
                    "candidate_version": candidate.version,
                    "source_channel": candidate.provenance.source_channel.value,
                },
            ),
        )
        self._outbox.add(
            connection,
            make_event(
                event_type=action,
                producer="skill_install_service",
                payload={
                    "candidate_id": candidate.candidate_id,
                    "candidate_version": candidate.version,
                    "lifecycle": candidate.lifecycle.value,
                    "correlation_id": auth.correlation_id,
                },
            ),
        )


__all__ = [
    "ProposeSkillCommand",
    "ProposedSkillMemberV1",
    "SkillInstallAuthorizationError",
    "SkillInstallService",
    "SkillInstallServiceError",
]
