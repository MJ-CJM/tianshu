"""ModeRouter：通道无关实现已迁至 core/mode_router.py，此处仅做飞书侧薄别名。

保留原导入路径，供 assistant_branch / edict_branch / card_action_dispatcher /
__init__ 等既有调用点继续 `from tianshu.gateway.feishu.mode_router import ...`
不变。
"""

from __future__ import annotations

from tianshu.gateway.core.mode_router import Mode, ModeContext, ModeRouter

__all__ = ["ModeRouter", "ModeContext", "Mode"]
