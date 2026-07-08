"""ContainerRunner — 代码变体沙箱的容器隔离薄封装（可选降级）。

天枢的「代码变体」进化会在 worktree 里改代码并跑评估。直接在宿主执行时，
变体有误伤宿主的风险。本模块加一层容器隔离：`--network none` + 只读挂载 +
CPU/内存限额。风险模型只需防「变体误伤宿主」，共享内核包一层即已足够。

用户已拍板「可选降级」：检测不到容器运行时就干净降级到宿主直跑，绝不因缺
容器而崩——`run()` 在无运行时时返回 {"degraded": True}，由调用方据此回退。

运行时探测顺序：docker（Docker Desktop / OrbStack 均提供 docker CLI）→
container（macOS 26 的 Apple container CLI）。命令按 docker CLI 形状构造；
Apple container 的 run 子命令假定同形（本项目仅在 docker 形状下验证，见
build_command 注释）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

# 按优先级探测的运行时 CLI：docker 覆盖 Docker Desktop 与 OrbStack，container 为 Apple。
_RUNTIME_CANDIDATES: tuple[str, ...] = ("docker", "container")

_DEFAULT_IMAGE = "python:3.12-slim"
_DEFAULT_MEMORY = "512m"
_DEFAULT_CPUS = "1.0"
_DEFAULT_TIMEOUT = 300.0
# 容器内挂载点：worktree 只读挂到此路径，命令的工作目录也设在此。
_CONTAINER_WORKDIR = "/workspace"


class ContainerRunner:
    """构造并执行「容器内跑一条命令」的薄封装；无运行时则降级，绝不抛异常。"""

    def detect_runtime(self) -> str | None:
        """按优先级探测可用的容器运行时 CLI，返回其名称；都没有则返回 None。"""
        for name in _RUNTIME_CANDIDATES:
            if shutil.which(name):
                return name
        return None

    def is_available(self) -> bool:
        """是否存在可用的容器运行时。"""
        return self.detect_runtime() is not None

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
        """构造 `docker run --rm ...` 调用（纯列表构造，不真的起容器，便于测试）。

        隔离手段：--network none（可关）、--read-only（可关）、worktree 只读挂载
        (:ro)、--memory / --cpus 限额、-w 工作目录设于挂载点。

        运行时取 detect_runtime()，缺失时回退到首选 docker——命令按 docker CLI
        形状构造；Apple container 的 run 子命令假定同形（本项目仅在 docker 形状
        下验证，若 Apple container 的 flag 有别需在此做最小适配）。
        """
        runtime = self.detect_runtime() or _RUNTIME_CANDIDATES[0]
        args: list[str] = [runtime, "run", "--rm"]
        if network_none:
            args += ["--network", "none"]
        if readonly:
            args.append("--read-only")
        args += [
            "-v",
            f"{workdir}:{_CONTAINER_WORKDIR}:ro",
            "--memory",
            memory,
            "--cpus",
            cpus,
            "-w",
            _CONTAINER_WORKDIR,
            image,
        ]
        args += cmd
        return args

    def run(
        self,
        cmd: list[str],
        workdir: str,
        *,
        image: str = _DEFAULT_IMAGE,
        memory: str = _DEFAULT_MEMORY,
        cpus: str = _DEFAULT_CPUS,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict[str, object]:
        """在容器内执行 cmd；无运行时则降级返回，超时/异常均捕获成结构化 dict。

        无运行时：{"degraded": True, "reason": "no_container_runtime"}——调用方据此
        回退到宿主直跑。可用时：{"degraded": False, "returncode", "stdout", "stderr"}；
        超时补 "timed_out": True，其他执行异常补 "error": True（returncode 皆为 None）。
        """
        if not self.is_available():
            return {"degraded": True, "reason": "no_container_runtime"}
        argv = self.build_command(cmd, workdir, image=image, memory=memory, cpus=cpus)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("容器执行超时(%.0fs)：%s", timeout, argv[:3])
            return {
                "degraded": False,
                "returncode": None,
                "stdout": _coerce(exc.stdout),
                "stderr": _coerce(exc.stderr),
                "timed_out": True,
            }
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.warning("容器执行失败：%s", exc)
            return {
                "degraded": False,
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
                "error": True,
            }
        return {
            "degraded": False,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


def _coerce(raw: str | bytes | None) -> str:
    """把 TimeoutExpired 上可能为 bytes/None 的捕获输出统一成 str。"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return raw
