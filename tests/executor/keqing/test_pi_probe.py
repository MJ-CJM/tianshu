"""Pi warm probes use governed gateway RPC and always clean up the handle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tianshu.executor.keqing.pi_probe import verify_pi_rpc_contract


def _pi_install(tmp_path: Path) -> Path:
    package = tmp_path / "pi-package"
    executable = package / "bin" / "pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "bin/pi"},
            }
        )
    )
    return executable


class _ProbeHandle:
    def __init__(
        self,
        mode: str = "success",
        *,
        cleanup_failure: bool = False,
    ) -> None:
        self.mode = mode
        self.cleanup_failure = cleanup_failure
        self.command_id: str | None = None
        self.closed = False
        self.terminated = False
        self._never = asyncio.Event()
        self.command_written = asyncio.Event()

    async def write_stdin(self, payload: bytes) -> None:
        command = json.loads(payload)
        assert command["type"] == "get_session_stats"
        self.command_id = command["id"]
        self.command_written.set()

    async def iter_stdout_bytes(self):
        if self.mode == "timeout":
            await self._never.wait()
            return
        if self.mode == "bad-json":
            yield b"not-json\n"
            return
        version = 999 if self.mode == "bad-version" else 3
        frames = [
            {"type": "session", "version": version, "id": "probe", "cwd": "/tmp"},
            {
                "type": "response",
                "id": self.command_id,
                "command": "get_session_stats",
                "success": self.mode != "rejected",
                "data": {"tokens": {"input": 0, "output": 0}, "cost": 0.0},
                "error": "rejected" if self.mode == "rejected" else None,
            },
        ]
        payload = b"".join((json.dumps(frame) + "\n").encode() for frame in frames)
        midpoint = len(payload) // 2
        yield payload[:midpoint]
        yield payload[midpoint:]

    async def close_stdin(self) -> None:
        self.closed = True
        self._never.set()
        if self.cleanup_failure:
            raise RuntimeError("close failed")

    async def terminate(self) -> None:
        self.terminated = True


class _ProbeGateway:
    def __init__(self, handle: _ProbeHandle) -> None:
        self.handle = handle
        self.requests = []

    async def start(self, request):
        self.requests.append(request)
        return self.handle


async def test_probe_validates_header_and_side_effect_free_stats_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _pi_install(tmp_path)
    handle = _ProbeHandle()
    gateway = _ProbeGateway(handle)

    async def direct_spawn_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("probe bypassed ExecutionGateway")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", direct_spawn_forbidden)

    result = await verify_pi_rpc_contract(
        gateway,  # type: ignore[arg-type]
        workspace_root=tmp_path,
        binary_path=str(executable.resolve()),
        timeout_seconds=1,
    )

    assert result == (True, None)
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.command_argv[0] == str(executable.resolve())
    assert request.command_grant.scope == "keqing"
    assert request.stdin_mode == "pipe"
    assert handle.closed is True
    assert handle.terminated is True


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("bad-json", "invalid_json_frame"),
        ("bad-version", "unsupported_wire_version"),
        ("rejected", "stats_rejected"),
    ],
)
async def test_probe_rejects_bad_frames_and_cleans_up(
    tmp_path: Path,
    mode: str,
    reason: str,
) -> None:
    executable = _pi_install(tmp_path)
    handle = _ProbeHandle(mode)

    result = await verify_pi_rpc_contract(
        _ProbeGateway(handle),  # type: ignore[arg-type]
        workspace_root=tmp_path,
        binary_path=str(executable.resolve()),
        timeout_seconds=1,
    )

    assert result == (False, reason)
    assert handle.closed is True
    assert handle.terminated is True


async def test_probe_timeout_still_cleans_up(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    handle = _ProbeHandle("timeout")

    result = await verify_pi_rpc_contract(
        _ProbeGateway(handle),  # type: ignore[arg-type]
        workspace_root=tmp_path,
        binary_path=str(executable.resolve()),
        timeout_seconds=0.01,
    )

    assert result == (False, "timeout")
    assert handle.closed is True
    assert handle.terminated is True


async def test_probe_cancellation_still_cleans_up(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    handle = _ProbeHandle("timeout")
    task = asyncio.create_task(
        verify_pi_rpc_contract(
            _ProbeGateway(handle),  # type: ignore[arg-type]
            workspace_root=tmp_path,
            binary_path=str(executable.resolve()),
            timeout_seconds=30,
        )
    )
    await asyncio.wait_for(handle.command_written.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert handle.closed is True
    assert handle.terminated is True


async def test_probe_reports_cleanup_failure(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    handle = _ProbeHandle(cleanup_failure=True)

    result = await verify_pi_rpc_contract(
        _ProbeGateway(handle),  # type: ignore[arg-type]
        workspace_root=tmp_path,
        binary_path=str(executable.resolve()),
        timeout_seconds=1,
    )

    assert result == (False, "cleanup_failed")
    assert handle.terminated is True
