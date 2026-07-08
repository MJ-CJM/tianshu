"""起居注官 Diarist —— 从用户行为信号蒸馏「主人偏好」画像(迭代 4「记忆 2.0」)。

profile_synthesizer 是 per-persona 官员成长画像(官员擅长什么);起居注补上**用户
维度**——主人习惯什么、喜欢什么。把它接进自进化闭环后,fitness 从"客观绩效"
升级为"主人满意度",自进化方向贴合真实使用者(护城河加深)。

信号源(全部已落库,零新增采集面):
- 批红行为(Decree action:approve/reject/amend + comment);
- feedback_score(+1/-1)、runtime/acceptance override(follow-up 修正=嫌哪里不对);
- edict 提交模式(任务域/时段/频率)。

产出:
- USER_PROFILE.md(Markdown 真相源,与记忆宫殿同构);
- 偏好三元组入时序 KG(scope=user,经校勘门——偏好漂移可表达、过时偏好可作废)。

与后台史官共用蒸馏管线(史官蒸馏执行知识,起居注蒸馏用户习惯,同 cron 同机制)。
隐喻:起居注即史官记录皇帝言行之册。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager
    from tianshu.memory.kg import KnowledgeGraph
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_PROFILE_FILE = "USER_PROFILE.md"

_DISTILL_PROMPT = """你是起居注官,负责根据主人的操作信号,提炼出「主人的使用偏好」。

批红行为统计:{decree_stats}
反馈信号:{feedback_stats}
近期任务样本:{task_samples}

请输出两部分,用 `---TRIPLES---` 分隔:
1. 一段简洁的《起居注·主人偏好》(Markdown,不超过 200 字):主人偏好的风格、
   常做的任务域、批红习惯。
2. 分隔线后,每行一条偏好三元组,格式 `谓词|宾语`(主语固定为 user),例如:
   `prefers|简洁输出`
   `works_on|Python 项目`
   `review_habit|多数直接批准`
只输出这两部分,不要额外解释。信号不足时三元组部分可为空。"""


class Diarist:
    def __init__(
        self,
        storage: Storage,
        config_manager: ConfigManager,
        kg: KnowledgeGraph,
        memory_dir: Path | None = None,
    ) -> None:
        self._storage = storage
        self._config_manager = config_manager
        self._kg = kg
        self._memory_dir = Path(memory_dir or Path("~/.tianshu/memory").expanduser())

    async def synthesize(self) -> bool:
        """蒸馏一次用户画像;信号不足返回 False(不覆盖已有画像)。"""
        signals = self._gather_signals()
        if signals["total_signals"] < 3:
            logger.debug("[diarist] signals too sparse (%d), skip", signals["total_signals"])
            return False
        profile_md, triples = await self._distill(signals)
        if profile_md:
            self._write_profile(profile_md)
        now = datetime.now(UTC).isoformat()
        for predicate, obj in triples:
            self._kg.assert_fact("user", predicate, obj, scope="user", source="profile", now=now)
        logger.info("[diarist] user profile synthesized (%d preference triples)", len(triples))
        return True

    def _gather_signals(self) -> dict:
        decrees = self._storage.list_recent_decrees(50)
        decree_actions = Counter(d.action for d in decrees)
        memorials, _ = self._storage.list_memorials(limit=50)
        feedback: Counter[str] = Counter()
        overrides = 0
        for m in memorials:
            fb = getattr(m, "feedback_score", 0)
            if fb > 0:
                feedback["up"] += 1
            elif fb < 0:
                feedback["down"] += 1
            if getattr(m, "runtime_override", None) or getattr(m, "acceptance_override", None):
                overrides += 1
        edicts, _ = self._storage.list_edicts(limit=30, exclude_assistant_chat=True)
        task_samples = [e.goal[:60] for e in edicts[:10]]
        total = sum(decree_actions.values()) + sum(feedback.values()) + overrides
        return {
            "decree_stats": dict(decree_actions),
            "feedback_stats": {**dict(feedback), "override_corrections": overrides},
            "task_samples": task_samples,
            "total_signals": total,
        }

    async def _distill(self, signals: dict) -> tuple[str, list[tuple[str, str]]]:
        from tianshu.llm import LLMClient

        state = self._config_manager.state
        llm = LLMClient(
            model=state.model,
            api_key=state.api_key,
            api_base=state.api_base,
            temperature=0.4,
            max_tokens=512,
        )
        resp = await llm.chat(
            [
                {"role": "system", "content": "你是简洁的起居注官。"},
                {
                    "role": "user",
                    "content": _DISTILL_PROMPT.format(
                        decree_stats=json.dumps(signals["decree_stats"], ensure_ascii=False),
                        feedback_stats=json.dumps(signals["feedback_stats"], ensure_ascii=False),
                        task_samples=json.dumps(signals["task_samples"], ensure_ascii=False),
                    ),
                },
            ]
        )
        text = (resp.content or "").strip()
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> tuple[str, list[tuple[str, str]]]:
        if "---TRIPLES---" in text:
            profile_part, triples_part = text.split("---TRIPLES---", 1)
        else:
            profile_part, triples_part = text, ""
        triples: list[tuple[str, str]] = []
        for line in triples_part.strip().splitlines():
            line = line.strip().lstrip("-").strip()
            if "|" in line:
                predicate, obj = line.split("|", 1)
                predicate, obj = predicate.strip(), obj.strip()
                if predicate and obj:
                    triples.append((predicate, obj))
        return profile_part.strip(), triples

    def _write_profile(self, content: str) -> None:
        target = self._memory_dir / "court" / _PROFILE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_profile(self) -> str:
        target = self._memory_dir / "court" / _PROFILE_FILE
        return target.read_text(encoding="utf-8") if target.exists() else ""
