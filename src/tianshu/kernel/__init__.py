"""跨层共享契约与执行上下文——不依赖任何业务模块（仅可依赖 tianshu.models）。"""

from tianshu.kernel.ambient import bind_edict, bind_persona, get_current_edict, get_current_persona
from tianshu.kernel.exit_reason import ExitReason
from tianshu.kernel.hooks import HookHandler, HookRegistry, HookResult, HookType

__all__ = [
    "HookType",
    "HookResult",
    "HookRegistry",
    "HookHandler",
    "ExitReason",
    "get_current_edict",
    "get_current_persona",
    "bind_edict",
    "bind_persona",
]
