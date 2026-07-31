"""CodeMutator — 在代码变体 worktree 内，按假设让 LLM 改写演化域内的某个代码文件。

是 Phase 1 人格 mutator 的代码版：失败安全（任何异常/空输出→no-op，不留半截改动），
allowlist 强制（演化域外的目标直接拒绝），traversal-safe（只能写 worktree 内）。
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from tianshu.executor.git_backend import GitBackend, GitIdentity, GitLocation

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是改写单个 Python 源文件的工程师，只输出该文件的完整新内容，不要解释、不要 markdown 围栏"
)

_USER = """\
假设：{hypothesis}

目标文件：{target_path}

当前文件内容：
---
{old_content}
---

请输出改写后的完整文件全文（{target_path}）。"""


def _within_evolvable(rel_path: str, evolvable_paths: tuple[str, ...]) -> bool:
    """rel_path 是否落在演化域 allowlist 内（精确文件 或 目录前缀，目录以 / 结尾）。"""
    rel = rel_path.lstrip("/")
    for p in evolvable_paths:
        if p.endswith("/"):
            if rel.startswith(p):
                return True
        elif rel == p:
            return True
    return False


def _is_concrete_python_target(rel_path: str) -> bool:
    """Return whether a target is a normalized repository-relative Python file."""
    if not rel_path or rel_path.endswith("/"):
        return False
    path = PurePosixPath(rel_path)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.suffix == ".py"
        and path.as_posix() == rel_path
    )


def _strip_code_fence(text: str) -> str:
    """剥掉 LLM 可能返回的 ```lang\\n...\\n``` 围栏，返回围栏内内容（保留尾部换行）。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    # 找第一行（``` 或 ```python 等）
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return text
    inner = stripped[first_newline + 1 :]
    # 剥掉结尾的 ```
    if inner.endswith("```"):
        inner = inner[:-3]
    elif "\n```" in inner:
        inner = inner.rsplit("\n```", 1)[0]
    return inner.rstrip() + "\n"


class CodeMutator:
    def __init__(
        self,
        llm_client,
        *,
        evolvable_paths: tuple[str, ...],
        git_backend: GitBackend | None = None,
    ) -> None:
        self._llm = llm_client
        self._evolvable = tuple(evolvable_paths)
        self._git_backend = git_backend or GitBackend()

    def is_within_evolvable(self, rel_path: str) -> bool:
        return _is_concrete_python_target(rel_path) and _within_evolvable(rel_path, self._evolvable)

    async def mutate(self, worktree: Path, *, target_path: str, hypothesis: str) -> dict:
        """改写 worktree 内 target_path 文件以回应 hypothesis；失败安全 no-op。

        返回 {"applied": bool, "target": str, "commit": str|None, "reason": str}。
        """
        wt = Path(worktree).resolve()

        if not self.is_within_evolvable(target_path):
            return {
                "applied": False,
                "target": target_path,
                "commit": None,
                "reason": "target outside evolvable allowlist",
            }

        abs_target = (wt / target_path).resolve()
        try:
            abs_target.relative_to(wt)
        except ValueError:
            return {
                "applied": False,
                "target": target_path,
                "commit": None,
                "reason": "path traversal rejected",
            }

        if not abs_target.is_file():
            return {
                "applied": False,
                "target": target_path,
                "commit": None,
                "reason": "target file not found",
            }

        try:
            old = abs_target.read_text(encoding="utf-8")
            new = await self._ask_llm(target_path, old, hypothesis)
        except Exception as e:  # noqa: BLE001
            logger.warning("code mutate LLM failed: %s", e)
            return {
                "applied": False,
                "target": target_path,
                "commit": None,
                "reason": f"llm error: {e}",
            }

        if not new or not new.strip() or new.strip() == old.strip():
            return {
                "applied": False,
                "target": target_path,
                "commit": None,
                "reason": "empty or unchanged output",
            }

        try:
            abs_target.write_text(new, encoding="utf-8")
            location = GitLocation(wt)
            self._git_backend.stage_paths(location, (target_path,))
            sha = self._git_backend.commit(
                location,
                f"evolve: {hypothesis[:60]}",
                identity=GitIdentity("evolver", "evolver@tianshu"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("code mutate write/commit failed: %s", e)
            return {
                "applied": False,
                "target": target_path,
                "commit": None,
                "reason": f"write/commit error: {e}",
            }

        return {"applied": True, "target": target_path, "commit": sha, "reason": "ok"}

    async def _ask_llm(self, target_path: str, old: str, hypothesis: str) -> str:
        """调 LLM 改写文件；若返回有 markdown 围栏则剥掉。"""
        resp = await self._llm.chat(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": _USER.format(
                        hypothesis=hypothesis,
                        target_path=target_path,
                        old_content=old,
                    ),
                },
            ]
        )
        raw = (getattr(resp, "content", None) or "").strip()
        return _strip_code_fence(raw) if raw else ""
