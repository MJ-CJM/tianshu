"""锦衣卫 —— 运行时安全监察(迭代 3「深防御」)。

四件套:出站脱敏(redact)/ 子进程 clean-env / bash 风险分级(在
tools.policy_rules.bash_safety)/ 分级急停(estop)。
"""

from tianshu.security.clean_env import build_clean_env
from tianshu.security.estop import EstopManager, EstopState
from tianshu.security.redact import redact_mapping, redact_text

__all__ = [
    "EstopManager",
    "EstopState",
    "build_clean_env",
    "redact_mapping",
    "redact_text",
]
