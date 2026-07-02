"""官员（persona）工具 ACL 的通配符匹配。

设计：
- ``tools_allowed`` / ``tools_denied`` 中的条目支持 ``fnmatch`` 通配符（``*``、
  ``?``、``[abc]``），让 SOUL.md 用 ``mcp_github_*`` 一行授权整个 MCP server。
- 不区分大小写不变（保持与精确匹配一致）。
- 评估顺序：先看 deny（命中即拒），再看 allow（非空时必须命中），最后回退到
  ``tier <= tool_tier_max``。

之所以单独抽出文件而不是直接放进 ``persona/model.py``，是为了让 ``gateway/api``
和未来的 Executor ACL hook 可以共享同一份语义。
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.persona.model import AgentPersona


def matches_any(patterns: list[str], tool_name: str) -> bool:
    """``patterns`` 中任一通配符命中 ``tool_name`` 时返回 True。"""
    return any(fnmatchcase(tool_name, pat) for pat in patterns)


def persona_can_use(
    persona: AgentPersona, tool_name: str, tool_tier: int
) -> bool:
    """判断给定 persona 能否调用名为 ``tool_name`` (tier=``tool_tier``) 的工具。

    决策顺序：
    1. ``tools_denied`` 命中 → 拒绝
    2. ``tools_allowed`` 非空 → 必须命中才放行
    3. 否则按 ``tool_tier <= persona.tool_tier_max`` 放行
    """
    if persona.tools_denied and matches_any(persona.tools_denied, tool_name):
        return False
    if persona.tools_allowed:
        return matches_any(persona.tools_allowed, tool_name)
    return tool_tier <= persona.tool_tier_max
