"""Governed long-lived sandbox process for Universe evaluation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
import sys
import urllib.request
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ulid import ULID

from tianshu.executor.execution_gateway import (
    ArgvCommand,
    EnvironmentPolicy,
    EnvironmentSecretRef,
    EnvironmentValue,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionHandle,
    ExecutionReceipt,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStartError,
    NetworkPolicy,
    SandboxRequirement,
    bind_execution_context,
    issue_universe_command_grant,
)
from tianshu.universe.execution import UniverseExecutionContextFactory

logger = logging.getLogger(__name__)

_SECRET_REFERENCE = re.compile(r"^\$\{([^{}]+)\}$")
_FIXED_ENV_NAMES = frozenset(
    {"PYTHONPATH", "TIANSHU_DB_PATH", "TIANSHU_PORT", "TIANSHU_HOST", "TIANSHU_EVAL_MODE"}
)
_LITERAL_EXTRA_NAMES = frozenset(
    {
        "TIANSHU_RUNTIME_PERSONAS_DIR",
        "TIANSHU_RUNTIME_SKILLS_DIR",
        "TIANSHU_LLM_API_BASE",
        "TIANSHU_LLM_MODEL",
    }
)


@dataclass
class SandboxHandle:
    port: int
    base_url: str
    db_path: Path
    execution: ExecutionHandle
    monitor: asyncio.Task[ExecutionResult]
    _result: ExecutionResult | None = None
    _stop_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def pid(self) -> int:
        return self.execution.pid

    @property
    def receipt(self) -> ExecutionReceipt | None:
        if self._result is not None:
            return self._result.receipt
        if self.monitor.done() and not self.monitor.cancelled():
            with contextlib.suppress(Exception):
                return self.monitor.result().receipt
        return None


class SandboxError(RuntimeError):
    def __init__(self, detail: str, *, receipt: ExecutionReceipt | None = None) -> None:
        self.receipt = receipt
        super().__init__(detail)


class SandboxRunner:
    def __init__(
        self,
        execution_gateway: ExecutionGateway,
        *,
        context_factory: UniverseExecutionContextFactory,
        python_exe: str | None = None,
        host: str = "127.0.0.1",
        startup_timeout_s: int = 60,
        runtime_timeout_s: int = 900,
    ) -> None:
        if startup_timeout_s <= 0 or runtime_timeout_s <= 0:
            raise ValueError("sandbox timeouts must be positive")
        if host != "127.0.0.1":
            raise ValueError("Universe sandbox host must remain loopback")
        self.execution_gateway = execution_gateway
        self.context_factory = context_factory
        self._py = python_exe or sys.executable
        self._host = host
        self._startup_timeout = startup_timeout_s
        self._runtime_timeout = runtime_timeout_s
        self.last_receipt: ExecutionReceipt | None = None
        self._active: dict[int, SandboxHandle] = {}
        self._starting: set[asyncio.Task[object]] = set()
        self._pending_cleanup: set[Path] = set()
        self._closing = False

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _build_environment(
        self,
        worktree: Path,
        db_path: Path,
        port: int,
        extra_env: Mapping[str, str] | None = None,
    ) -> EnvironmentPolicy:
        extras = {name: str(value) for name, value in (extra_env or {}).items()}
        fixed_overlap = _FIXED_ENV_NAMES.intersection(extras)
        if fixed_overlap:
            raise SandboxError("sandbox fixed environment variables cannot be overridden")

        values = [
            EnvironmentValue(name="PYTHONPATH", value=str(Path(worktree).resolve() / "src")),
            EnvironmentValue(name="TIANSHU_DB_PATH", value=str(Path(db_path))),
            EnvironmentValue(name="TIANSHU_PORT", value=str(port)),
            EnvironmentValue(name="TIANSHU_HOST", value=self._host),
            EnvironmentValue(name="TIANSHU_EVAL_MODE", value="1"),
        ]
        secret_refs: list[EnvironmentSecretRef] = []
        for name, value in extras.items():
            match = _SECRET_REFERENCE.fullmatch(value)
            if match is not None:
                secret_refs.append(EnvironmentSecretRef(env_name=name, ref=match.group(1)))
                continue
            if name not in _LITERAL_EXTRA_NAMES:
                raise SandboxError(
                    "sandbox extra environment must use an admitted literal name or secret reference"
                )
            values.append(EnvironmentValue(name=name, value=value))
        return EnvironmentPolicy(
            allow_names=(),
            values=tuple(values),
            secret_refs=tuple(secret_refs),
        )

    async def start(
        self,
        worktree: Path,
        *,
        db_path: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> SandboxHandle:
        if self._closing:
            raise SandboxError("sandbox runner is closing")
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio always supplies one in a coroutine
            raise RuntimeError("sandbox start requires an asyncio task")
        self._starting.add(task)
        try:
            return await self._start(worktree, db_path=db_path, extra_env=extra_env)
        except BaseException:
            try:
                self._remove_database_files(Path(db_path))
            except OSError:
                self._pending_cleanup.add(Path(db_path))
                logger.exception("sandbox database cleanup deferred for %s", db_path)
            raise
        finally:
            self._starting.discard(task)

    async def _start(
        self,
        worktree: Path,
        *,
        db_path: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> SandboxHandle:
        wt = Path(worktree).resolve()
        port = self._free_port()
        environment = self._build_environment(wt, db_path, port, extra_env=extra_env)
        context = self.context_factory.create(
            operation="sandbox",
            timeout_seconds=self._runtime_timeout,
            secret_refs=tuple(ref.ref for ref in environment.secret_refs),
        )
        argv = (
            self._py,
            "-m",
            "uvicorn",
            "tianshu.app:create_app",
            "--factory",
            "--host",
            self._host,
            "--port",
            str(port),
        )
        with bind_execution_context(context):
            grant = issue_universe_command_grant(
                stage="sandbox:serve",
                argv=argv,
                workspace_root=wt,
                cwd=".",
                environment=environment,
            )
        request = ExecutionRequest(
            execution_id=str(ULID()),
            correlation_id=context.correlation_id,
            actor=context.actor,
            purpose="universe_sandbox",
            universe_stage="sandbox:serve",
            effective_contract=context.effective_contract,
            argv_command=ArgvCommand(argv=argv),
            workspace_lease_id=context.workspace_lease_id,
            workspace_root=wt,
            cwd=".",
            environment=environment,
            network=NetworkPolicy(
                mode=(
                    "unrestricted"
                    if self.context_factory.security_mode == "trusted-local"
                    else "deny"
                )
            ),
            timeout_seconds=self._runtime_timeout,
            stdout_limit_bytes=64 * 1024,
            stderr_limit_bytes=64 * 1024,
            sandbox=SandboxRequirement(
                trust_level=self.context_factory.security_mode,
                mode=(
                    "host" if self.context_factory.security_mode == "trusted-local" else "required"
                ),
                allow_host=self.context_factory.security_mode == "trusted-local",
            ),
            command_grant=grant,
        )
        try:
            execution = await self.execution_gateway.start(request)
        except ExecutionStartError as exc:
            self.last_receipt = exc.receipt
            raise SandboxError("sandbox process failed to start", receipt=exc.receipt) from None
        except ExecutionDenied as exc:
            self.last_receipt = exc.receipt
            raise

        monitor = asyncio.create_task(execution.wait(), name=f"universe-sandbox-{port}")
        handle = SandboxHandle(
            port=port,
            base_url=f"http://{self._host}:{port}",
            db_path=Path(db_path),
            execution=execution,
            monitor=monitor,
        )
        self._active[handle.pid] = handle
        try:
            if not await self._await_health(handle):
                result = await self.stop(handle)
                raise SandboxError(
                    f"sandbox failed to become healthy on {handle.base_url}",
                    receipt=result.receipt,
                )
        except asyncio.CancelledError:
            await asyncio.shield(self.stop(handle))
            raise
        return handle

    async def _await_health(self, handle: SandboxHandle) -> bool:
        deadline = asyncio.get_running_loop().time() + self._startup_timeout
        url = f"{handle.base_url}/health"
        while asyncio.get_running_loop().time() < deadline:
            if handle.monitor.done():
                return False
            try:
                healthy = await asyncio.to_thread(self._probe_health, url)
                if healthy:
                    return True
            except Exception:  # noqa: BLE001 - an unavailable endpoint is not healthy yet
                pass
            await asyncio.sleep(0.1)
        return False

    @staticmethod
    def _probe_health(url: str) -> bool:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
            return response.status == 200

    async def stop(self, handle: SandboxHandle) -> ExecutionResult:
        async with handle._stop_lock:
            if handle._result is None:
                if handle.monitor.done() and not handle.monitor.cancelled():
                    try:
                        handle._result = handle.monitor.result()
                        await handle.execution.terminate()
                    except Exception:
                        handle._result = await handle.execution.terminate()
                else:
                    handle.monitor.cancel()
                    await asyncio.gather(handle.monitor, return_exceptions=True)
                    handle._result = await handle.execution.terminate()
            self.last_receipt = handle._result.receipt
            self._remove_database_files(handle.db_path)
            self._active.pop(handle.pid, None)
            return handle._result

    async def shutdown(self) -> None:
        self._closing = True
        current = asyncio.current_task()
        starting = tuple(task for task in self._starting if task is not current)
        for task in starting:
            task.cancel()
        if starting:
            await asyncio.gather(*starting, return_exceptions=True)

        handles = tuple(self._active.values())
        if handles:
            results = await asyncio.gather(
                *(self.stop(handle) for handle in handles),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.error(
                        "sandbox cleanup remains pending: %s",
                        result,
                        exc_info=(type(result), result, result.__traceback__),
                    )

        for db_path in tuple(self._pending_cleanup):
            try:
                self._remove_database_files(db_path)
            except OSError:
                logger.exception("sandbox database cleanup still pending for %s", db_path)
            else:
                self._pending_cleanup.discard(db_path)

    @staticmethod
    def _remove_database_files(db_path: Path) -> None:
        failures: list[OSError] = []
        for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(exc)
        if failures:
            raise failures[0]

    @asynccontextmanager
    async def session(
        self,
        worktree: Path,
        *,
        db_path: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> AsyncIterator[SandboxHandle]:
        handle = await self.start(worktree, db_path=db_path, extra_env=extra_env)
        try:
            yield handle
        finally:
            await asyncio.shield(self.stop(handle))


__all__ = ["SandboxError", "SandboxHandle", "SandboxRunner"]
