"""Multi-strategy context compaction for the agent loop."""

from tianshu.executor.compaction.auto import auto_compact, should_auto_compact
from tianshu.executor.compaction.micro import micro_compact
from tianshu.executor.compaction.reactive import reactive_compact
from tianshu.executor.compaction.token_estimator import estimate_tokens

__all__ = [
    "auto_compact",
    "estimate_tokens",
    "micro_compact",
    "reactive_compact",
    "should_auto_compact",
]
