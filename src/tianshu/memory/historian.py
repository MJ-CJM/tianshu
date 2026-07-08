"""后台史官 —— 从已 audited 的成功 memorial 蒸馏可复用执行知识(迭代 4「记忆 2.0」)。

与 Reflector(对话中反思)互补:史官是**后台异步**、**零对话 token** 的知识沉淀——
由 cron 周期扫描成功终态的 memorial,取其事件时间线,LLM 蒸馏成一条「下次遇到
同类任务可复用」的执行知识,写入 court 共享记忆。已蒸馏的记 historian_log 防重复。

隐喻:史官修实录——把发生过的事提炼成后人可鉴的记载。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tianshu.memory.models import MemoryEntry

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_DISTILL_PROMPT = """你是史官,负责把一次已完成任务提炼成可复用的执行知识。

任务目标:{goal}

执行事件时间线(节选):
{timeline}

请用一句话(不超过 40 字)提炼「下次遇到同类任务时值得记住的一条经验」——
可以是有效的方法、踩过的坑、或达成目标的关键步骤。只输出这一句,不要解释。
若这次任务平淡无奇、无可复用经验,只输出:SKIP"""

# 事件时间线里对蒸馏有价值的类型(工具调用/迭代/审计),过滤噪音
_USEFUL_EVENTS = {
    "tool.completed",
    "tool.failed",
    "iteration.started",
    "edict.audit.executed",
    "execution.completed",
}


class Historian:
    def __init__(
        self,
        storage: Storage,
        config_manager: ConfigManager,
        md_backend: object | None = None,
    ) -> None:
        self._storage = storage
        self._config_manager = config_manager
        self._md_backend = md_backend

    async def distill_recent(self, limit: int = 10) -> int:
        """扫描未蒸馏的成功 memorial,逐个蒸馏并写记忆。返回处理条数。"""
        memorials = self._storage.list_undistilled_memorials(limit)
        processed = 0
        for m in memorials:
            try:
                insight = await self._distill_one(m)
            except Exception:
                logger.exception("[historian] distill failed for memorial %s", m.id)
                continue
            now = datetime.now(UTC).isoformat()
            if insight:
                self._storage.save_memory_entry(
                    MemoryEntry(
                        persona_id="court",
                        edict_id=m.edict_id,
                        memorial_id=m.id,
                        category="insight",
                        content=insight,
                        source="reflection",
                        access_level="court",
                    )
                )
            self._storage.mark_distilled(m.id, bool(insight), now)
            processed += 1
        if processed:
            logger.info("[historian] distilled %d memorial(s)", processed)
        return processed

    async def _distill_one(self, memorial) -> str | None:
        edict = self._storage.get_edict(memorial.edict_id)
        goal = edict.goal if edict else (memorial.instruction or "")
        events = self._storage.get_events(memorial.edict_id)
        useful = [e for e in events if e.get("event_type") in _USEFUL_EVENTS]
        if not useful:
            return None
        timeline = "\n".join(
            f"- {e.get('event_type')}: {str(e.get('payload', {}))[:80]}" for e in useful[:15]
        )

        from tianshu.llm import LLMClient

        state = self._config_manager.state
        llm = LLMClient(
            model=state.model,
            api_key=state.api_key,
            api_base=state.api_base,
            temperature=0.4,
            max_tokens=128,
        )
        resp = await llm.chat(
            [
                {"role": "system", "content": "你是简洁的史官,只输出一句可复用经验。"},
                {"role": "user", "content": _DISTILL_PROMPT.format(goal=goal, timeline=timeline)},
            ]
        )
        text = (resp.content or "").strip()
        if not text or text.upper().startswith("SKIP"):
            return None
        return text
