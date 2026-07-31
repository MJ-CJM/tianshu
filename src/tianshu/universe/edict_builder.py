"""把太医诊断提案编译成「需求规格 + 验收物」的演化 edict(自进化外包核心)。

P5:evolver 的生成段从「本地 LLM 直接改代码」改为「产出 edict 交客卿(keqing:pi)开发」。
本模块是纯函数,不依赖 evolver 状态,可单测。evolver 注入 edict_application_service 后
用它构建 edict 再下发;branch/eval/灰度/廷议骨架保留。

演化域防护做成三重保险(删本地变异三件套的硬前置):
① edict.constraints 文本(演化域 allowlist);② P4 guard tool_call deny(路径外写入);
③ 验收 bash check 校验 git diff 路径 ⊆ allowlist。本模块负责 ①③。
"""

from __future__ import annotations

from tianshu.models.acceptance import AcceptanceCriteria, CheckSpec
from tianshu.models.edict import Edict, EdictRuntime

_DEFAULT_FOLLOW_UP_ROUNDS = 3


def build_evolution_edict(
    *,
    target_path: str,
    hypothesis: str,
    rationale: str = "",
    failure_symptoms: str = "",
    evolvable_paths: tuple[str, ...] = (),
    cost_budget_cny: float | None = None,
    base_timeout_seconds: int = 600,
    follow_up_rounds: int = _DEFAULT_FOLLOW_UP_ROUNDS,
    executor_model: str | None = None,
) -> Edict:
    """构建一个演化开发 edict,交 keqing:pi 客卿执行、天枢验收。

    - goal = 假设(要客卿实现/验证的改动)
    - context = rationale + 失败症状(诊断依据)
    - constraints = 演化域 allowlist 文本 + 不得破坏测试 + diff 须落在 target_path
    - acceptance = pytest 回归(bash)+ 演化域越界校验(bash)+ 改动契合假设(rubric)
    - runtime = keqing:pi + 预算 + follow_up 轮次预留的超时
    """
    allow_text = ", ".join(evolvable_paths) if evolvable_paths else target_path
    constraints = [
        f"只允许修改演化域内的文件:{allow_text}。域外文件一律不得改动。",
        "不得破坏现有测试;所有回归测试须通过。",
        f"本次改动应聚焦于:{target_path}。",
    ]

    checks = [
        CheckSpec(
            kind="bash",
            name="regression",
            command="python -m pytest -q",
            timeout_seconds=base_timeout_seconds,
        ),
        # 演化域越界校验:git diff 的改动路径须全部落在 allowlist 内,否则验收失败。
        CheckSpec(
            kind="bash",
            name="evolvable_scope",
            command=_scope_check_command(evolvable_paths or (target_path,)),
            timeout_seconds=60,
        ),
        CheckSpec(
            kind="rubric",
            name="hypothesis_fit",
            rubric=(f"改动是否切实实现/验证了以下假设,且未引入无关变更?假设:{hypothesis}"),
            pass_threshold=0.8,
        ),
    ]

    context_parts = [p for p in (rationale, failure_symptoms) if p]
    return Edict(
        title=f"演化:{target_path}",
        goal=hypothesis,
        context="\n\n".join(context_parts) or None,
        constraints=constraints,
        acceptance=AcceptanceCriteria(
            checks=checks,
            max_outer_iterations=follow_up_rounds,
        ),
        runtime=EdictRuntime(
            executor="keqing:pi",
            executor_model=executor_model,
            timeout_seconds=base_timeout_seconds,
            cost_budget_cny=cost_budget_cny,
        ),
    )


def _scope_check_command(evolvable_paths: tuple[str, ...]) -> str:
    """生成 bash:git diff 改动路径须全部 ⊆ evolvable_paths,越界即 exit 1。"""
    # 前缀匹配:每个改动文件必须以某个 allowlist 前缀开头。
    prefixes = " ".join(f"'{p}'" for p in evolvable_paths)
    return (
        "set -e; "
        "changed=$(git diff --name-only HEAD; git ls-files --others --exclude-standard); "
        f"allow=({prefixes}); "
        'for f in $changed; do ok=0; for p in "${allow[@]}"; do '
        'case "$f" in "$p"*) ok=1;; esac; done; '
        'if [ "$ok" != "1" ]; then echo "out-of-scope change: $f" >&2; exit 1; fi; done; '
        'echo "all changes within evolvable scope"'
    )
