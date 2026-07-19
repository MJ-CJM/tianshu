"""Run maintainer-only host commands through the single process boundary."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tianshu.executor.execution_gateway import (
    AsyncioProcessBackend,
    NetworkPolicy,
    SandboxRequirement,
)


@dataclass(frozen=True)
class TrustedLocalProcessResult:
    stdout: bytes
    stderr: bytes
    returncode: int

    @property
    def output(self) -> bytes:
        return self.stdout + self.stderr


async def _run_trusted_local_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
) -> TrustedLocalProcessResult:
    spawned = await AsyncioProcessBackend().spawn(
        argv=tuple(argv),
        cwd=cwd,
        env=dict(os.environ if env is None else env),
        network=NetworkPolicy(mode="unrestricted"),
        sandbox=SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        stdin_mode="null",
        on_spawned=lambda _spawned: None,
    )
    stdout, stderr = await spawned.process.communicate()
    returncode = spawned.process.returncode
    assert returncode is not None
    return TrustedLocalProcessResult(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


def run_trusted_local_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> TrustedLocalProcessResult:
    """Run one trusted maintainer command on the host and collect both streams."""

    return asyncio.run(_run_trusted_local_process(argv, cwd=cwd, env=env))
