"""客卿执行器 —— 反向驱动外部 coding agent(Claude Code / Codex)。

天枢保留全部治理面(规划/批红/审计/预算/成本归因/记忆沉淀),执行面成为
可插拔 backend。自研引擎仍是默认执行器;客卿是"自执行引擎 + 可选外部执行器",
配合 MCP server 化(外部 agent 驱动天枢)构成双向互操作。

v1(迭代 3.5,MVP):headless CLI 适配(Claude Code `claude -p --output-format
stream-json` / Codex `codex exec`),隔离 worktree + clean-env(客卿用自身凭证)
+ 预算熔断 + 产出照常走 memorial → 审计 → 批红管线。
"""

from tianshu.executor.keqing.adapter import (
    KeqingAdapter,
    KeqingRunResult,
    get_adapter,
    list_adapters,
)
from tianshu.executor.keqing.executor import KeqingExecutor, parse_keqing_backend

__all__ = [
    "KeqingAdapter",
    "KeqingExecutor",
    "KeqingRunResult",
    "get_adapter",
    "list_adapters",
    "parse_keqing_backend",
]
