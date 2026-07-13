"""源码守卫：运行时资源消费者不得回归 repo-root 路径推断。

G1.5 之前有九处 ``Path(__file__).parent...`` 推断仓库根来定位资源，wheel
安装后全部失效。切换到 ``tianshu.resources`` 后，本守卫锁定：
``src/tianshu`` 内不得再出现四级 ``.parent`` 链（repo-root 推断的特征模式）。

``wiring_universe``/``cli/commands/evals`` 的 ``parents[N]`` 属显式配置的
开发模式回退（非资源定位），在白名单内。
"""

import re
from pathlib import Path

import tianshu

_SRC_ROOT = Path(tianshu.__file__).parent

_FOUR_PARENT_CHAIN = re.compile(r"\.parent\.parent\.parent\.parent")

_PARENTS_INDEX = re.compile(r"__file__\)\.resolve\(\)\.parents\[|__file__\)\.parents\[")
_PARENTS_ALLOWLIST = {
    # doctor 的 repo-root 显式配置检查需读取推断路径来生成告警证据
    "diagnostics.py",
    # 显式设置为空时的开发模式回退，均有对应 TIANSHU_* 显式配置项
    "bootstrap/wiring_universe.py",
    "cli/commands/evals.py",
}


def _py_files() -> list[Path]:
    return [p for p in _SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_four_level_parent_chain_resource_inference() -> None:
    offenders = [
        str(p.relative_to(_SRC_ROOT))
        for p in _py_files()
        if _FOUR_PARENT_CHAIN.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"repo-root 推断回归：{offenders}。资源定位必须走 tianshu.resources "
        "(catalog/overlay)，可写路径必须走显式 data 根配置。"
    )


def test_parents_index_inference_stays_in_allowlist() -> None:
    offenders = [
        str(p.relative_to(_SRC_ROOT))
        for p in _py_files()
        if _PARENTS_INDEX.search(p.read_text(encoding="utf-8"))
        and str(p.relative_to(_SRC_ROOT)) not in _PARENTS_ALLOWLIST
    ]
    assert offenders == [], (
        f"新增 parents[N] 仓库根推断：{offenders}。若为资源请走 tianshu.resources；"
        "若为工作区/评测根请提供显式 TIANSHU_* 配置并把回退登记进白名单。"
    )
