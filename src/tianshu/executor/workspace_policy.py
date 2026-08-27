"""Compatibility exports for workspace policy validation owned by models."""

from tianshu.models.workspace_policy import (
    WORKSPACE_MAIN_SOURCE_ID,
    WorkspacePolicyMismatch,
    WorkspacePolicyValidationError,
    WorkspaceRoots,
    validate_workspace_policy,
    validate_workspace_roots,
    workspace_policy_mismatches,
)

__all__ = [
    "WORKSPACE_MAIN_SOURCE_ID",
    "WorkspacePolicyMismatch",
    "WorkspacePolicyValidationError",
    "WorkspaceRoots",
    "validate_workspace_policy",
    "validate_workspace_roots",
    "workspace_policy_mismatches",
]
