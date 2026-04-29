"""LLM 意图解析。仅在助手模式 + 命令未命中时调用。"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.persona.loader import PersonaLoader
    from tianshu.persona.model import AgentPersona
    from tianshu.providers.manager import ProviderManager

logger = logging.getLogger(__name__)

INTENTS = ("new", "list", "status", "cancel", "budget", "help", "unknown")


def _find_json_block(text: str) -> str | None:
    """从 text 中提取第一个完整的 {...} 块（支持一层嵌套，按括号深度匹配）。

    朴素的非贪婪正则在遇到 {"args": {}} 这类嵌套时会过早闭合，
    所以这里手写一个 brace-counter。字符串内出现的 { } 会被忽略以避免误判。
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


class IntentParser:
    """轻量 LLM 意图分类器。返回 {"intent": ..., "args": {...}}。

    - persona 缺失 / 无 LLM 配置 / 调用失败 / JSON 无效 / intent 不在白名单
      → 全部返回 {"intent": "unknown", "args": {}}，不抛异常
    """

    def __init__(
        self,
        *,
        persona_loader: PersonaLoader,
        provider_manager: ProviderManager,
        persona_id: str,
    ) -> None:
        self._loader = persona_loader
        self._providers = provider_manager
        self._persona_id = persona_id

    def set_persona(self, persona_id: str) -> None:
        self._persona_id = persona_id

    async def parse(self, text: str) -> dict:
        persona = self._loader.get(self._persona_id)
        if persona is None:
            logger.warning("[feishu/intent] persona %s not found", self._persona_id)
            return {"intent": "unknown", "args": {}}
        if not persona.llm_config_name:
            logger.warning(
                "[feishu/intent] persona %s has no llm_config_name", persona.name,
            )
            return {"intent": "unknown", "args": {}}

        # ProviderManager.get_client(config_name_override=...) → LLMClient
        try:
            client = self._providers.get_client(
                config_name_override=persona.llm_config_name,
            )
        except Exception:
            logger.exception("[feishu/intent] get_client failed")
            return {"intent": "unknown", "args": {}}

        prompt = self._build_prompt(persona)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ]
        try:
            # LLMClient.chat(messages, tools=None) → LLMResponse(content=str)
            response = await client.chat(messages=messages)
        except Exception:
            logger.exception("[feishu/intent] LLM call failed")
            return {"intent": "unknown", "args": {}}

        resp_text = getattr(response, "content", None) or ""
        return self._parse_json(resp_text)

    @staticmethod
    def _build_prompt(persona: AgentPersona) -> str:
        return (
            f"你是用户的飞书助手「{persona.name}」。判断用户消息想做什么。\n"
            f"只输出一个 JSON：{{\"intent\": \"<one of {list(INTENTS)}>\", \"args\": {{...}}}}\n\n"
            "intent 含义：\n"
            "- new: 用户想新建一个敕令（args.goal 为目标描述）\n"
            "- list: 用户想看敕令列表（args.filter ∈ open/completed/all）\n"
            "- status: 用户想看某敕令状态（args.target 为 id 前缀或 'latest'）\n"
            "- cancel: 用户想取消某敕令（args.target 为 id 前缀或 'latest'）\n"
            "- budget: 用户想看成本/预算\n"
            "- help: 用户想看帮助\n"
            "- unknown: 不属于以上任何一种\n\n"
            "示例：\n"
            "\"显示我的列表\" → {\"intent\": \"list\", \"args\": {}}\n"
            "\"取消最近那个\" → {\"intent\": \"cancel\", \"args\": {\"target\": \"latest\"}}\n"
            "\"新建一个敕令做摘要\" → {\"intent\": \"new\", \"args\": {\"goal\": \"做摘要\"}}\n"
            "\"在干啥\" → {\"intent\": \"unknown\", \"args\": {}}\n"
            "\"花了多少钱\" → {\"intent\": \"budget\", \"args\": {}}"
        )

    @staticmethod
    def _parse_json(resp_text: str) -> dict:
        if not resp_text:
            return {"intent": "unknown", "args": {}}
        block = _find_json_block(resp_text)
        if block is None:
            return {"intent": "unknown", "args": {}}
        try:
            obj = json.loads(block)
        except Exception:
            logger.warning("[feishu/intent] invalid JSON: %.200s", resp_text)
            return {"intent": "unknown", "args": {}}
        if not isinstance(obj, dict):
            return {"intent": "unknown", "args": {}}
        intent = obj.get("intent", "unknown")
        if intent not in INTENTS:
            logger.warning("[feishu/intent] invalid intent: %s", intent)
            return {"intent": "unknown", "args": {}}
        args = obj.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        return {"intent": intent, "args": args}


__all__ = ["INTENTS", "IntentParser"]
