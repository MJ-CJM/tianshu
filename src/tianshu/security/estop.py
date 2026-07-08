"""分级急停 EstopManager —— 全停 / 掐网 / 冻结工具(借鉴 zeroclaw estop.rs)。

三档语义(可叠加):
- **kill_all**:工具管线入口全拒 —— 运行中的任务在下一次工具调用即被切断,
  比进程级强杀温和且可恢复(spec:工具管线入口检查)。
- **network_kill**:网络类工具(鸿胪寺四件套 / lark_cli)+ shell 类全拒
  (shell 可 curl,掐网必须连带)。
- **tool_freeze**:冻结指定工具名列表。

状态持久化为 SQLite 单行(estop_state 表 id=1);读取损坏 fail-closed
(视为 kill_all)。engage/resume 全部事件留痕(producer="estop")。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

# network_kill 拦截面:鸿胪寺网络工具 + 飞书透传 + shell 类(可发起任意网络)
NETWORK_TOOL_NAMES = frozenset(
    {"web_fetch", "web_search", "api_request", "web_extract", "lark_cli", "shell_exec", "bash"}
)


@dataclass(frozen=True)
class EstopState:
    kill_all: bool = False
    network_kill: bool = False
    frozen_tools: frozenset[str] = field(default_factory=frozenset)
    updated_at: str | None = None
    reason: str | None = None

    @property
    def engaged(self) -> bool:
        return self.kill_all or self.network_kill or bool(self.frozen_tools)

    @classmethod
    def fail_closed(cls) -> EstopState:
        """状态读取失败时的兜底:宁可全停,不可失控(zeroclaw 同款语义)。"""
        return cls(kill_all=True, reason="estop state corrupted — fail closed")

    def to_dict(self) -> dict:
        return {
            "kill_all": self.kill_all,
            "network_kill": self.network_kill,
            "frozen_tools": sorted(self.frozen_tools),
            "updated_at": self.updated_at,
            "reason": self.reason,
            "engaged": self.engaged,
        }


class EstopManager:
    """急停状态的读写与工具级判定;工具管线入口每次调用都过 check()。

    状态读一次缓存在内存,写操作(engage/resume)同步刷库+刷缓存——
    急停在人命关天的路径上,check() 不能每次都打 SQLite。
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._state: EstopState = self._load()

    # --- 判定(工具管线入口热路径) ---

    def check(self, tool_name: str) -> str | None:
        """返回拒绝原因;None = 放行。"""
        s = self._state
        if not s.engaged:
            return None
        if s.kill_all:
            return "emergency stop engaged (kill_all): all tool calls are rejected"
        if s.network_kill and tool_name in NETWORK_TOOL_NAMES:
            return f"emergency stop engaged (network_kill): tool '{tool_name}' is network-capable"
        if tool_name in s.frozen_tools:
            return f"emergency stop engaged (tool_freeze): tool '{tool_name}' is frozen"
        return None

    def status(self) -> EstopState:
        return self._state

    # --- 状态变更 ---

    def engage(
        self,
        *,
        kill_all: bool | None = None,
        network_kill: bool | None = None,
        freeze_tools: list[str] | None = None,
        reason: str | None = None,
    ) -> EstopState:
        """叠加式收紧:给定的档位开启,未给定的保持现状。"""
        from datetime import UTC, datetime

        cur = self._state
        new = EstopState(
            kill_all=cur.kill_all if kill_all is None else kill_all,
            network_kill=cur.network_kill if network_kill is None else network_kill,
            frozen_tools=cur.frozen_tools | frozenset(freeze_tools or ()),
            updated_at=datetime.now(UTC).isoformat(),
            reason=reason or cur.reason,
        )
        self._persist(new)
        logger.warning("[estop] engaged: %s", new.to_dict())
        return new

    def resume(
        self,
        *,
        kill_all: bool = False,
        network_kill: bool = False,
        unfreeze_tools: list[str] | None = None,
        all_clear: bool = False,
    ) -> EstopState:
        """选择性恢复;all_clear=True 一键全解。"""
        from datetime import UTC, datetime

        if all_clear:
            new = EstopState(updated_at=datetime.now(UTC).isoformat())
        else:
            cur = self._state
            new = EstopState(
                kill_all=False if kill_all else cur.kill_all,
                network_kill=False if network_kill else cur.network_kill,
                frozen_tools=cur.frozen_tools - frozenset(unfreeze_tools or ()),
                updated_at=datetime.now(UTC).isoformat(),
                reason=cur.reason,
            )
        self._persist(new)
        logger.warning("[estop] resumed: %s", new.to_dict())
        return new

    # --- 持久化 ---

    def _load(self) -> EstopState:
        try:
            row = self._storage.get_estop_state()
        except Exception:
            logger.exception("[estop] state load failed — fail closed")
            return EstopState.fail_closed()
        if row is None:
            return EstopState()
        try:
            return EstopState(
                kill_all=bool(row["kill_all"]),
                network_kill=bool(row["network_kill"]),
                frozen_tools=frozenset(json.loads(row["frozen_tools_json"] or "[]")),
                updated_at=row["updated_at"],
                reason=row["reason"],
            )
        except Exception:
            logger.exception("[estop] state corrupted — fail closed")
            return EstopState.fail_closed()

    def _persist(self, state: EstopState) -> None:
        self._storage.save_estop_state(
            {
                "kill_all": state.kill_all,
                "network_kill": state.network_kill,
                "frozen_tools_json": json.dumps(sorted(state.frozen_tools)),
                "updated_at": state.updated_at,
                "reason": state.reason,
            }
        )
        self._state = state
