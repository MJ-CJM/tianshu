"""Shared path sandbox utilities."""

from __future__ import annotations

import os
from pathlib import Path


def safe_path(workspace: Path, path_str: str) -> Path:
    """Ensure path does not escape the workspace."""
    resolved = (workspace / path_str).resolve()
    workspace_resolved = workspace.resolve()
    workspace_prefix = str(workspace_resolved) + os.sep
    if resolved != workspace_resolved and not str(resolved).startswith(
        workspace_prefix
    ):
        raise PermissionError(f"Path '{path_str}' is outside workspace")
    return resolved
