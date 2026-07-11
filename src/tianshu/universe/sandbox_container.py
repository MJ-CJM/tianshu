"""Unverified container command descriptor for future sandbox backends.

Runtime presence is not isolation proof.  This module intentionally does not
launch a process or advertise enforcement until a real gateway backend can
attest each spawned process.
"""

from __future__ import annotations

import shutil

_RUNTIME_CANDIDATES: tuple[str, ...] = ("docker", "container")
_DEFAULT_IMAGE = "python:3.12-slim"
_DEFAULT_MEMORY = "512m"
_DEFAULT_CPUS = "1.0"
_CONTAINER_WORKDIR = "/workspace"


class ContainerRunner:
    """Build container argv while remaining explicitly unavailable for execution."""

    verified = False

    def detect_runtime(self) -> str | None:
        for name in _RUNTIME_CANDIDATES:
            if shutil.which(name):
                return name
        return None

    def is_available(self) -> bool:
        """Return enforcement availability, not mere CLI presence."""

        return False

    def build_command(
        self,
        cmd: list[str],
        workdir: str,
        *,
        image: str = _DEFAULT_IMAGE,
        memory: str = _DEFAULT_MEMORY,
        cpus: str = _DEFAULT_CPUS,
        readonly: bool = True,
        network_none: bool = True,
    ) -> list[str]:
        runtime = self.detect_runtime() or _RUNTIME_CANDIDATES[0]
        args: list[str] = [runtime, "run", "--rm"]
        if network_none:
            args += ["--network", "none"]
        if readonly:
            args.append("--read-only")
        return [
            *args,
            "-v",
            f"{workdir}:{_CONTAINER_WORKDIR}:ro",
            "--memory",
            memory,
            "--cpus",
            cpus,
            "-w",
            _CONTAINER_WORKDIR,
            image,
            *cmd,
        ]

    def run(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "available": False,
            "sandbox_enforced": False,
            "reason": "unverified_container_backend",
        }


__all__ = ["ContainerRunner"]
