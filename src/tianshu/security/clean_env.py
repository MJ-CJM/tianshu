"""子进程 clean-env —— shell 类工具不透传父进程全量环境变量。

天枢进程 env 里有 TIANSHU_LLM_API_KEY / TIANSHU_SECRET_MASTER_KEY 等全部
secrets;`shell_exec` 若继承全量 env,一句 `echo $TIANSHU_LLM_API_KEY`
即泄漏。白名单借鉴 zeroclaw SAFE_ENV_VARS + shell_env_passthrough:
默认只透传终端/locale/路径类,业务需要的额外变量经
``TIANSHU_SHELL_ENV_PASSTHROUGH``(逗号分隔)显式声明。

MCP stdio 子进程无需在此处理:官方 SDK 在 env=None 时用
get_default_environment() 白名单,天枢仅在用户显式配置 server env 时透传
(用户显式给的是有意为之)——该边界由 tests/security 锚定。
"""

from __future__ import annotations

import os
import re

# zeroclaw SAFE_ENV_VARS 同款,补 PWD(cwd 语义)
SAFE_ENV_VARS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "TERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "USER",
    "SHELL",
    "TMPDIR",
    "PWD",
)

_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_clean_env(
    passthrough: str | None = None, *, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """白名单构造子进程 env。

    passthrough: 逗号分隔的额外变量名(默认读 TIANSHU_SHELL_ENV_PASSTHROUGH);
    非法变量名(防注入)与源 env 中不存在的名字跳过。
    """
    source = os.environ if base_env is None else base_env
    if passthrough is None:
        passthrough = source.get("TIANSHU_SHELL_ENV_PASSTHROUGH", "")

    allowed: list[str] = list(SAFE_ENV_VARS)
    for name in passthrough.split(","):
        name = name.strip()
        if name and _VALID_NAME.match(name) and name not in allowed:
            allowed.append(name)

    return {k: source[k] for k in allowed if k in source}
