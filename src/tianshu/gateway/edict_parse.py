"""自然语言 → 敕令草稿 的解析核心（纯函数，便于测试）。

端点 POST /api/edicts/parse 用 LLM 把自然语言转结构化草稿，这里负责：
prompt 构造、从 LLM 输出里提取 JSON、对草稿做防御式校验/清洗。
不创建敕令，无副作用。
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from croniter import croniter

_VALID_SCHEDULE = ("immediate", "once", "cron")
_VALID_PRIORITY = ("urgent", "normal", "low")

PARSE_SYSTEM_PROMPT = """你是天枢的敕令解析器。把用户的中文自然语言需求转成一个 JSON 对象，描述要下发的敕令。

只输出 JSON，不要任何解释文字、不要 markdown 代码围栏。JSON 字段（除 goal 外都可省略）：
- "goal": 字符串，敕令旨意（必填，命令式，描述要做的事）
- "title": 字符串，简短标题（可省略）
- "context": 字符串，补充背景/约束（可省略）
- "schedule": 对象，调度。形如：
    立即执行: {"type":"immediate"}
    一次性:   {"type":"once","at":"2026-05-21 14:30:00"}     // 本地北京时间，不带时区后缀
    周期:     {"type":"cron","cron":"0 18 * * *","timezone":"Asia/Shanghai"}
  用户没提到时间就省略 schedule（默认立即）。"每天X点"→cron；"明天/某具体时刻做一次"→once。
- "priority": "urgent" | "normal" | "low"（没提到就省略）
- "notes": 字符串，一句话中文说明你识别到了什么（时间、调度类型等），供用户核对。

时间一律按北京时间（Asia/Shanghai）理解。只填用户明确表达的字段，其余一律省略。
不要臆造 goal 以外的内容。"""


def build_parse_messages(text: str) -> list[dict]:
    """构造给 LLM 的消息。"""
    return [
        {"role": "system", "content": PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]


class ParseEdictError(Exception):
    """LLM 输出无法解析为 JSON。"""


def parse_llm_json(content: str) -> dict:
    """从 LLM 输出里提取 JSON 对象。容忍 ```json 围栏与前后噪声。"""
    if not content or not content.strip():
        raise ParseEdictError("empty LLM output")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ParseEdictError("no JSON object in LLM output")
        candidate = content[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ParseEdictError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ParseEdictError("LLM JSON is not an object")
    return data


def coerce_draft(raw: dict) -> tuple[dict, str]:
    """把 LLM 的原始 dict 清洗成安全的草稿 + notes。

    丢弃非法字段而不报错；goal 缺失时返回空 goal（前端仍要求用户填）。
    时间字段不在此补时区——交给创建端点既有的 naive→Asia/Shanghai 逻辑。
    """
    draft: dict = {}

    goal = raw.get("goal")
    if isinstance(goal, str) and goal.strip():
        draft["goal"] = goal.strip()

    for key in ("title", "context"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            draft[key] = v.strip()

    prio = raw.get("priority")
    if isinstance(prio, str) and prio in _VALID_PRIORITY:
        draft["priority"] = prio

    sched_notes = ""
    sched = raw.get("schedule")
    if isinstance(sched, dict):
        stype = sched.get("type")
        if stype in _VALID_SCHEDULE:
            clean: dict = {"type": stype}
            if stype == "cron":
                cron = sched.get("cron")
                if isinstance(cron, str) and croniter.is_valid(cron):
                    clean["cron"] = cron
                    clean["timezone"] = (
                        sched.get("timezone")
                        if isinstance(sched.get("timezone"), str)
                        else "Asia/Shanghai"
                    )
                else:
                    clean = {}
                    sched_notes = "（未识别出合法的周期表达式，请手动填写）"
            elif stype == "once":
                at = sched.get("at")
                ok = False
                if isinstance(at, str):
                    try:
                        datetime.fromisoformat(at)
                        ok = True
                    except (TypeError, ValueError):
                        ok = False
                if ok:
                    clean["at"] = at
                else:
                    clean = {}
                    sched_notes = "（未识别出合法的执行时间，请手动填写）"
            if clean:
                draft["schedule"] = clean

    notes = raw.get("notes")
    notes_str = notes.strip() if isinstance(notes, str) and notes.strip() else ""
    notes_str = (notes_str + sched_notes).strip()
    return draft, notes_str
