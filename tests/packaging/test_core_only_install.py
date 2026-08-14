"""核心发行物（``tianshu-agent-os[cli]``）的可导入性守卫。

这类缺陷开发环境里永远看不见：可选依赖被别的 extra 传递带入（lxml 由 scrapling/
trafilatura 带来、lark_oapi 由 feishu extra 带来），于是全部单测都绿，而只装
``tianshu-agent-os[cli]`` 的用户 API 起不来。S1.5 黑盒抓到第一个（duckduckgo 模块级
``import lxml`` 让 uvicorn 加载 app 即失败）后，这里把它变成一条常设防线。

做法：**在子进程里**把可选依赖从 import 系统摘掉，再走真实的导入/调用路径。
必须是子进程——在本进程里动 ``sys.modules`` 会把 ``tianshu.*`` 的类身份换掉，
后续测试拿到的就是另一批 pydantic 模型（实测会让同一批次的 executor 测试报
ValidationError）。守卫本身不该污染别人。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# 不在 core + cli 依赖里、只由可选 extra 提供的顶层模块。
_OPTIONAL_TOPLEVEL = (
    "lxml",
    "lark_oapi",
    "telegram",
    "mcp",
    "scrapling",
    "trafilatura",
    "aiosmtplib",
    "opentelemetry",
)

# 核心发行物里必须能导入的模块（app + 全部 gateway router + CLI 入口）。
_CORE_IMPORTABLE = (
    "tianshu.app",
    "tianshu.cli.main",
    "tianshu.gateway.tongzheng_api",
    "tianshu.gateway.system_api",
    "tianshu.gateway.edicts_api",
    "tianshu.gateway.workspace_api",
    "tianshu.gateway.config_api",
    "tianshu.gateway.bot_manager",
    "tianshu.tools.hongluisi.engine_registry",
    "tianshu.diagnostics",
)

# 用 sys.meta_path finder 拦截，而不是覆盖 builtins.__import__：后者只拦 import
# 语句，importlib.import_module() 走 _bootstrap._gcd_import 会绕过去。今天 src/ 里
# 没有动态导入可选依赖的地方，所以两种写法结果相同——但将来有人加一个
# importlib.import_module("lxml")，__import__ 钩子会静默漏检，守卫就成了摆设。
_GUARD_PREAMBLE = f"""
import sys
from importlib.abc import MetaPathFinder

_OPTIONAL = {_OPTIONAL_TOPLEVEL!r}


class _BlockOptional(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if top in _OPTIONAL:
            raise ModuleNotFoundError("No module named " + repr(top), name=top)
        return None


sys.meta_path.insert(0, _BlockOptional())
for _name in list(sys.modules):
    if _name.split(".")[0] in _OPTIONAL:
        del sys.modules[_name]
"""


def _run_in_core_only_env(body: str) -> subprocess.CompletedProcess[str]:
    script = _GUARD_PREAMBLE + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("module_name", _CORE_IMPORTABLE)
def test_core_module_imports_without_optional_dependencies(module_name: str) -> None:
    result = _run_in_core_only_env(f"""
        import importlib
        importlib.import_module({module_name!r})
        print("OK")
    """)
    assert result.returncode == 0, (
        f"{module_name} 在核心发行物里 import 不到——把这个 import 惰性化，"
        f"否则 tianshu-agent-os[cli] 的用户装上就起不来：\n{result.stderr[-1500:]}"
    )


@pytest.mark.parametrize("view_name", ["_env_feishu_view", "_env_telegram_view"])
def test_channel_env_views_do_not_crash_without_their_extra(view_name: str) -> None:
    """渠道配置视图在缺 extra 时也必须能返回（此前直接 500）。

    这两个视图读的是纯 dataclass 配置，与 lark_oapi / python-telegram-bot 无关；
    它们曾因包 ``__init__`` 的模块级 import 而连坐。
    """
    result = _run_in_core_only_env(f"""
        from tianshu.gateway import tongzheng_api
        payload = getattr(tongzheng_api, {view_name!r})()
        assert isinstance(payload, dict), payload
        print("OK")
    """)
    assert result.returncode == 0, (
        f"{view_name}() 在核心发行物里崩了——渠道配置视图不该依赖机器人 extra：\n"
        f"{result.stderr[-1500:]}"
    )
