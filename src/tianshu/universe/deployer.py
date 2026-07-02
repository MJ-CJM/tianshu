"""Deployer — code 变体晋升/回滚：current_ref 指针 + 进程重启 + 健康检查自动回滚。

晋升 = 指针翻到变体（旧 current 入 previous）→ relaunch（os.execv 自重启）。
重启后查 /health；不健康 → 指针翻回 previous → relaunch（自动回滚兜底）。
真实 relaunch（os.execv）藏在可注入回调后，单测不真重启。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeployRecord:
    ref: str
    worktree: str | None

    def to_dict(self) -> dict:
        return {"ref": self.ref, "worktree": self.worktree}

    @classmethod
    def from_dict(cls, d: dict | None):
        if not d:
            return None
        return cls(ref=d["ref"], worktree=d.get("worktree"))


class DeployPointer:
    """持久化 {current, previous} 两条 DeployRecord 到一个 JSON 文件。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> tuple[DeployRecord | None, DeployRecord | None]:
        if not self._path.exists():
            return None, None
        d = json.loads(self._path.read_text(encoding="utf-8"))
        return DeployRecord.from_dict(d.get("current")), DeployRecord.from_dict(d.get("previous"))

    def write(self, current: DeployRecord | None, previous: DeployRecord | None) -> None:
        self._path.write_text(json.dumps({
            "current": current.to_dict() if current else None,
            "previous": previous.to_dict() if previous else None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_relaunch() -> None:
    """用 current_ref 指向的 worktree 重新 exec launcher（PID 不变，容器存活）；调用后不返回。"""
    os.execv(sys.executable, [sys.executable, "-m", "tianshu.universe.launcher"])


class Deployer:
    def __init__(
        self,
        pointer: DeployPointer,
        *,
        relaunch: Callable[[], None] | None = None,
        health_timeout_s: int = 30,
    ) -> None:
        self._pointer = pointer
        self._relaunch = relaunch or _default_relaunch
        self._health_timeout = health_timeout_s

    def stage(self, *, ref: str, worktree: str | None) -> None:
        """只写指针（旧 current → previous），不重启。供"先暂存、稍后受控重启"用。"""
        current, _prev = self._pointer.read()
        self._pointer.write(DeployRecord(ref=ref, worktree=worktree), current)
        logger.info("stage → ref=%s worktree=%s (prev=%s)", ref, worktree, current)

    def promote(self, *, ref: str, worktree: str | None) -> None:
        """暂存 + 立即重启。"""
        self.stage(ref=ref, worktree=worktree)
        self._relaunch()

    def rollback(self) -> None:
        current, previous = self._pointer.read()
        if previous is None:
            logger.warning("rollback skipped: no previous deploy record")
            return
        self._pointer.write(previous, None)
        logger.info("rollback → ref=%s (was %s)", previous.ref, current)
        self._relaunch()

    def health_ok(self, base_url: str) -> bool:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=3) as resp:  # noqa: S310
                return resp.status == 200
        except Exception:  # noqa: BLE001
            return False

    def await_health(self, base_url: str) -> bool:
        deadline = time.monotonic() + self._health_timeout
        while time.monotonic() < deadline:
            if self.health_ok(base_url):
                return True
            time.sleep(1)
        return False

    def verify_or_rollback(self, base_url: str) -> bool:
        if self.await_health(base_url):
            return True
        logger.error("post-deploy health check failed → auto rollback")
        self.rollback()
        return False
