"""Pure domain validation for governed workspace policy and root separation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tianshu.models.governance_contract import RecoveryPolicyV1, WorkspacePolicyV1

WORKSPACE_MAIN_SOURCE_ID = "workspace-main"
_UNSET = object()


@dataclass(frozen=True)
class WorkspacePolicyMismatch:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class WorkspacePolicyValidationError(ValueError):
    def __init__(self, mismatches: tuple[WorkspacePolicyMismatch, ...]) -> None:
        self.mismatches = mismatches
        super().__init__("; ".join(item.message for item in mismatches))


@dataclass(frozen=True)
class WorkspaceRoots:
    source: Path
    staging: Path


def workspace_policy_mismatches(
    workspace: WorkspacePolicyV1,
    recovery: RecoveryPolicyV1,
    *,
    resolved_source_id: str | None | object = _UNSET,
    resolved_base_revision: str | None | object = _UNSET,
) -> tuple[WorkspacePolicyMismatch, ...]:
    """Return one stable mismatch for an invalid workspace-mode matrix."""
    resolved_supplied = resolved_source_id is not _UNSET or resolved_base_revision is not _UNSET
    if workspace.staging_mode == "legacy_shared":
        invalid = (
            workspace.apply_mode != "none"
            or workspace.base_revision is not None
            or workspace.require_clean_source
            or recovery.require_restore_point
            or (
                resolved_supplied
                and (
                    resolved_source_id not in {None, workspace.source_id}
                    or resolved_base_revision is not None
                )
            )
        )
        if invalid:
            return (
                WorkspacePolicyMismatch(
                    code="legacy_shared_policy_invalid",
                    message=(
                        "legacy_shared requires no base, restore point, conflicting resolved "
                        "identity, clean-source gate, or apply"
                    ),
                ),
            )
        return ()

    if workspace.staging_mode == "ephemeral":
        invalid = (
            workspace.source_id is not None
            or workspace.base_revision is not None
            or workspace.apply_mode != "none"
            or workspace.require_clean_source
            or recovery.require_restore_point
            or (
                resolved_supplied
                and (resolved_source_id is not None or resolved_base_revision is not None)
            )
        )
        if invalid:
            return (
                WorkspacePolicyMismatch(
                    code="ephemeral_policy_invalid",
                    message=(
                        "ephemeral requires no source, base, restore point, resolved identity, "
                        "clean-source gate, or apply"
                    ),
                ),
            )
        return ()

    base = workspace.base_revision
    resolved_base = resolved_base_revision
    invalid = (
        workspace.source_id != WORKSPACE_MAIN_SOURCE_ID
        or not base
        or not base.strip()
        or workspace.apply_mode != "governed"
        or not workspace.require_clean_source
        or not recovery.require_restore_point
        or (
            resolved_supplied
            and (
                resolved_source_id != WORKSPACE_MAIN_SOURCE_ID
                or not isinstance(resolved_base, str)
                or not resolved_base.strip()
            )
        )
    )
    if invalid:
        return (
            WorkspacePolicyMismatch(
                code="isolated_policy_invalid",
                message=(
                    "isolated requires workspace-main, an explicit non-empty base, governed apply, "
                    "clean source, restore point, and matching resolved identity"
                ),
            ),
        )
    return ()


def validate_workspace_policy(
    workspace: WorkspacePolicyV1,
    recovery: RecoveryPolicyV1,
    *,
    resolved_source_id: str | None | object = _UNSET,
    resolved_base_revision: str | None | object = _UNSET,
) -> None:
    mismatches = workspace_policy_mismatches(
        workspace,
        recovery,
        resolved_source_id=resolved_source_id,
        resolved_base_revision=resolved_base_revision,
    )
    if mismatches:
        raise WorkspacePolicyValidationError(mismatches)


def validate_workspace_roots(
    source_root: str | Path,
    staging_root: str | Path,
) -> WorkspaceRoots:
    """Resolve roots without mutation and reject either direction of overlap."""
    source = Path(source_root).expanduser().resolve(strict=False)
    staging = Path(staging_root).expanduser().resolve(strict=False)
    if source == staging or staging.is_relative_to(source) or source.is_relative_to(staging):
        raise ValueError(
            "workspace source root and workspace staging root must be separate, non-nested paths"
        )
    return WorkspaceRoots(source=source, staging=staging)


__all__ = [
    "WORKSPACE_MAIN_SOURCE_ID",
    "WorkspacePolicyMismatch",
    "WorkspacePolicyValidationError",
    "WorkspaceRoots",
    "validate_workspace_policy",
    "validate_workspace_roots",
    "workspace_policy_mismatches",
]
