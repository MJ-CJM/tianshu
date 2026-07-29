"""内阁派官（CabinetDispatcher）——未指派官员的敕令由内阁按名册拣选执行官。

语义对齐「执行方式：内阁决策」：敕令未显式指派、规划也未给出非默认人选时，
内阁用一次轻量 LLM 调用（edict_parse 任务槽位，可指便宜模型）从百官名册中
选出最合适的官员；任何失败/超时/选出无效 id 都静默落回默认执行官——派官
永远不能阻断执行。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from tianshu.persona.model import DEFAULT_EXECUTOR_ID

if TYPE_CHECKING:
    from tianshu.models import Edict
    from tianshu.persona.loader import PersonaLoader
    from tianshu.providers.manager import ProviderManager

logger = logging.getLogger(__name__)

_DISPATCH_TIMEOUT_SECONDS = 20

_PROMPT_TEMPLATE = """你是天枢朝廷的内阁首辅，职责是为敕令拣选最合适的官员执行。

百官名册：
{roster}

敕令旨意：{goal}

拣选规则：
- 按官员的部门与职衔判断谁最适合办理此事（如历史/知识问题选学者型官员，
  工具执行/工程任务选执行型官员，审计核查选监察官员）。
- 无明显更合适者时选 "{default_id}"（通用执行官）。

只回复 JSON（不要多余文字）：{{"persona_id": "<名册中的 id>", "reason": "<一句话理由>"}}"""


class CabinetDispatcher:
    def __init__(self, persona_loader: PersonaLoader, provider_manager: ProviderManager) -> None:
        self._loader = persona_loader
        self._provider_manager = provider_manager

    async def dispatch(self, edict: Edict) -> tuple[str, str] | None:
        """返回 (persona_id, reason)；不派/失败返回 None（调用方落默认执行官）。"""
        personas = getattr(self._loader, "_personas", {})
        roster = [p for p in personas.values() if getattr(p, "id", None)]
        if len(roster) <= 1:
            return None

        roster_text = "\n".join(
            f"- id: {p.id} | 姓名: {p.name} | 部门: {p.department}"
            + (f" | 职衔: {p.title}" if getattr(p, "title", None) else "")
            for p in roster
        )
        prompt = _PROMPT_TEMPLATE.format(
            roster=roster_text,
            goal=(edict.goal or "")[:2000],
            default_id=DEFAULT_EXECUTOR_ID,
        )
        try:
            client = self._provider_manager.get_client_for_slot(
                "edict_parse", temperature=0.1, max_tokens=200
            )
            response = await asyncio.wait_for(
                client.chat([{"role": "user", "content": prompt}]),
                timeout=_DISPATCH_TIMEOUT_SECONDS,
            )
            parsed = self._extract_json(response.content or "")
        except Exception as exc:  # noqa: BLE001 - 派官失败绝不阻断执行
            logger.warning("[cabinet] dispatch failed, falling back to default: %s", exc)
            return None
        if not isinstance(parsed, dict):
            return None
        persona_id = str(parsed.get("persona_id") or "").strip()
        valid_ids = {p.id for p in roster} | {DEFAULT_EXECUTOR_ID}
        if persona_id not in valid_ids:
            logger.warning("[cabinet] dispatch picked unknown persona %r, ignoring", persona_id)
            return None
        reason = str(parsed.get("reason") or "").strip()[:200]
        return persona_id, reason

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        stripped = text.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
