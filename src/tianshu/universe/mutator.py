"""位面变异改写器：把演化引擎提出的「变异意图」真正落到候选位面的文件上。

cut 1：仅支持人格文件（SOUL.md / ROLE.md）改写——人格目录被位面完整快照、
切换时 restore+reload，故零改造即可端到端生效。policy / config / skillset 待后续。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PERSONA_PREFIX = "persona:"
_ALLOWED_FILES = {"SOUL.md", "ROLE.md"}
_MAX_CONTENT = 64 * 1024  # 单个改写后人格文件大小上限

_SYSTEM = (
    "你是天枢的「演化」改写官。给定某官员当前的人格文件与一条改写意图，"
    "产出改写后的【完整文件全文】（保持原体例：标题/小节结构），只做意图所指的定向调整，"
    "不要无关重写。只输出文件全文，不要任何代码块标记或解释。"
)

_USER = """\
官员：{persona_id}
文件：{filename}
改写意图：{reason}

当前文件内容：
---
{content}
---

请输出改写后的完整文件全文（{filename}）。"""


def parse_persona_target(target: str) -> tuple[str, str] | None:
    """解析 'persona:<id>/<FILE>' → (persona_id, filename)；非法/越白名单/穿越 → None。"""
    if not target or not target.startswith(_PERSONA_PREFIX):
        return None
    rest = target[len(_PERSONA_PREFIX) :].strip()
    if "/" not in rest:
        return None
    pid, _, fname = rest.partition("/")
    pid, fname = pid.strip(), fname.strip()
    if not pid or fname not in _ALLOWED_FILES:
        return None
    if "/" in pid or ".." in pid or pid.startswith("."):
        return None
    return pid, fname


async def apply_mutation(store: Any, child_id: str, mutation: dict, llm: Any) -> dict:
    """把 mutation 应用到候选位面 child 的文件。返回 {applied, target, detail}。

    cut 1：仅人格文件。非人格/非法 target → applied=False（记录而不改），不抛异常。
    """
    target = (mutation or {}).get("target") or ""
    parsed = parse_persona_target(target)
    if not parsed:
        return {
            "applied": False,
            "target": target,
            "detail": "unsupported target (only persona SOUL.md/ROLE.md in this cut)",
        }
    persona_id, filename = parsed
    pfile = store.personas_dir(child_id) / persona_id / filename
    if not pfile.exists():
        return {
            "applied": False,
            "target": target,
            "detail": f"file not found: {persona_id}/{filename}",
        }
    current = pfile.read_text(encoding="utf-8")
    reason = (mutation.get("reason") or "").strip()
    try:
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": _USER.format(
                        persona_id=persona_id, filename=filename, reason=reason, content=current
                    ),
                },
            ]
        )
        new_text = (getattr(resp, "content", None) or "").strip()
        if new_text.startswith("```") and "\n" in new_text:
            new_text = new_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("[MUTATOR] llm rewrite failed: %s", e)
        return {"applied": False, "target": target, "detail": f"llm error: {e}"}
    if not new_text or len(new_text) > _MAX_CONTENT:
        return {"applied": False, "target": target, "detail": "empty or oversized rewrite"}
    if new_text == current:
        return {"applied": False, "target": target, "detail": "no change produced"}
    pfile.write_text(new_text, encoding="utf-8")
    logger.info("[MUTATOR] rewrote %s/%s in universe %s", persona_id, filename, child_id)
    return {"applied": True, "target": target, "detail": f"rewrote {persona_id}/{filename}"}
