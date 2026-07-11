"""Public Universe docs must not claim unenforced resource isolation."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("relative_path", "misleading_claim"),
    [
        ("docs/ops/eval-harness.md", "内存 rlimit，跑完即 kill"),
        ("docs/design/universe/code-variant.md", "资源闸（内存 rlimit）"),
        (
            "docs/superpowers/specs/2026-06-08-code-variant-universe-design.md",
            "资源上限（wall timeout / 内存 rlimit / CPU）",
        ),
        (
            "docs/superpowers/specs/2026-06-08-code-variant-universe-design.md",
            "wall timeout / 内存 rlimit / CPU 上限",
        ),
        ("docs/reference/original-designs.md", "内存 rlimit，绝不碰生产 DB"),
        (
            "docs/superpowers/specs/2026-06-08-code-variant-universe-design.md",
            "隔离子进程（临时端口 + 隔离 DB 副本 + 临时目录 + 资源闸",
        ),
        (
            "docs/superpowers/specs/2026-06-08-code-variant-universe-design.md",
            "拉起隔离子进程（临时端口+隔离DB+资源闸",
        ),
        (
            "docs/superpowers/specs/2026-06-08-code-variant-universe-design.md",
            "资源闸**把评估期变体关在沙箱里",
        ),
        ("docs/design/universe/README.md", "EvalHarness 隔离子进程 + 隔离 DB"),
        ("docs/design/universe/code-variant.md", "SandboxRunner（隔离子进程）"),
        ("docs/design/universe/code-variant.md", "资源闸把评估期变体关在沙箱里"),
        (
            "docs/design/universe/eval.md",
            "隔离子进程 + 隔离 DB 副本 + `EVAL_MODE` 副作用围栏 + 资源闸",
        ),
        ("docs/design/universe/eval.md", "# 隔离子进程拉起变体"),
    ],
)
def test_docs_do_not_claim_unenforced_resource_isolation(
    relative_path: str,
    misleading_claim: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / relative_path).read_text(encoding="utf-8")

    assert misleading_claim not in text
