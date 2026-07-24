"""文件级 base/ours/theirs 三方合并(P7:客卿产出合回主仓的核心逻辑)。

WorkspaceApplyEngine 的 preimage 漂移当前是「整单放弃」;P7 升级为文件级三方裁决:
- ours==base(主仓没动过)→ 取 theirs(直接写入客卿产出);
- theirs==base(客卿没动)→ 保 ours(跳过);
- ours==theirs → 跳过(无差异);
- 双改 → git merge-file 尝试自动合并,无冲突则落地,有冲突结构化上报进批红(不落盘)。

本模块是纯逻辑 + git merge-file 薄封装,自包含可测;接入 workspace_apply 的事务核心
(fd 锚/CAS/journal)是 P7 的集成步骤。git merge-file 仅在临时区执行,source 对象库零变动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

ThreeWayDecision = Literal["take_theirs", "keep_ours", "skip", "merge", "conflict"]

# 注入式 git merge-file 运行器:(base, ours, theirs) → (returncode, merged_stdout)。
# 本模块**不直接启动进程**(遵守架构守卫:进程启动须走 GitBackend 的白名单站点);
# 生产由 git_backend 提供 sanctioned 实现,测试注入 subprocess 版(tests 豁免守卫)。
GitMergeFileRunner = Callable[[str, str, str], "tuple[int, str]"]


def classify_three_way(base: str | None, ours: str | None, theirs: str | None) -> ThreeWayDecision:
    """按 base/ours/theirs 内容分类(None = 文件不存在)。

    - ours == base 且 theirs != base → take_theirs(主仓没动,采纳客卿产出)
    - theirs == base 且 ours != base → keep_ours(客卿没动,保主仓)
    - ours == theirs → skip(双方一致,无需动作)
    - 三者互不相等(双改)→ merge(交 git merge-file 定夺)
    """
    if ours == theirs:
        return "skip"
    if ours == base:
        return "take_theirs"
    if theirs == base:
        return "keep_ours"
    return "merge"


@dataclass
class MergeOutcome:
    decision: ThreeWayDecision
    merged: str | None = None  # decision=merge 且无冲突时的合并结果
    conflict: bool = False


def merge_text(base: str, ours: str, theirs: str, *, git_merge_file: GitMergeFileRunner) -> MergeOutcome:
    """双改场景:经注入的 git merge-file 运行器做三方文本合并。

    运行器不碰任何真实仓库对象库(在临时区跑)。冲突时不返回带冲突标记的内容
    (交上层结构化上报三份全文进批红),只标 conflict=True。
    """
    returncode, merged = git_merge_file(base, ours, theirs)
    if returncode == 0:
        return MergeOutcome(decision="merge", merged=merged, conflict=False)
    return MergeOutcome(decision="conflict", conflict=True)


@dataclass
class FileConflict:
    """结构化冲突:三份全文上报批红(不做交互式 merge UI)。"""

    path: str
    base: str | None
    ours: str | None
    theirs: str | None


def resolve_file(
    path: str,
    base: str | None,
    ours: str | None,
    theirs: str | None,
    *,
    git_merge_file: GitMergeFileRunner,
) -> tuple[str | None, FileConflict | None]:
    """裁决单个文件。返回 (要落地的内容 | None=跳过, 冲突 | None)。

    - take_theirs → 落 theirs;keep_ours/skip → 跳过(None,None);
    - merge → 注入的 git merge-file:干净落 merged,冲突返回 FileConflict(不落盘)。
    - 新增/删除的不对称场景(base None 等)归入 merge 交 git 处理或标冲突。
    """
    decision = classify_three_way(base, ours, theirs)
    if decision == "take_theirs":
        return theirs, None
    if decision in ("keep_ours", "skip"):
        return None, None
    # merge:需三方文本;任一侧为 None(增/删不对称)则标冲突交人工。
    if base is None or ours is None or theirs is None:
        return None, FileConflict(path=path, base=base, ours=ours, theirs=theirs)
    outcome = merge_text(base, ours, theirs, git_merge_file=git_merge_file)
    if outcome.conflict:
        return None, FileConflict(path=path, base=base, ours=ours, theirs=theirs)
    return outcome.merged, None
