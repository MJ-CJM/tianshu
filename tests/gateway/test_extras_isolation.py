"""extras 独立性:无 lark_oapi 环境下 telegram 入口可导入(子进程黑名单验证)。

conftest 的 lark stub 只在测试进程生效;此处用独立子进程 + MetaPathFinder
黑名单模拟"未安装 lark_oapi"的真实环境。

注:黑名单用 find_spec（非 find_module/load_module）——本仓 Python 3.14 的
import 系统已不再回退调用旧式 find_module/load_module（PEP 451 之后的遗留兼容
路径已移除），用旧式钩子实测无论是否阻断 lark_oapi 都会静默放行，无法起到红/绿
判定作用；find_spec 返回一个 exec_module 即抛 ImportError 的 loader，可靠复现
"环境未装 lark_oapi"。
"""

import subprocess
import sys

_BOOT = """
import sys
import importlib.machinery

class _BlockLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        raise ImportError(f"blacklisted: {module.__name__}")

class _Block:
    def find_spec(self, name, path=None, target=None):
        if name == "lark_oapi" or name.startswith("lark_oapi."):
            return importlib.machinery.ModuleSpec(name, _BlockLoader())
        return None

sys.meta_path.insert(0, _Block())
import tianshu.gateway.telegram  # noqa: E402,F401
print("TELEGRAM_OK")
"""


def test_telegram_importable_without_lark():
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert "TELEGRAM_OK" in proc.stdout
