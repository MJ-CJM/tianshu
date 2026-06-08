"""代码变体位面的 git worktree 生命周期（branch / diff / gc / restore）。

与 UniverseStore（行为配置的文件拷贝快照）正交：CodeVariantStore 只管"代码层"——
每个代码变体位面 = 主仓的一条分支 `universe/<id>` + 一份 worktree。
git 是代码的唯一真相源；fork 起点 start_ref 记在 sidecar，供 diff / restore 复用。
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_BRANCH_PREFIX = "universe/"
_META = "_meta"


class CodeVariantStore:
    def __init__(self, repo_root: Path, worktrees_root: Path) -> None:
        self._repo = Path(repo_root).expanduser().resolve()
        self._root = Path(worktrees_root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / _META).mkdir(parents=True, exist_ok=True)

    # --- paths ---

    def worktree_dir(self, universe_id: str) -> Path:
        return self._root / universe_id

    def branch_name(self, universe_id: str) -> str:
        return f"{_BRANCH_PREFIX}{universe_id}"

    def exists(self, universe_id: str) -> bool:
        return self.worktree_dir(universe_id).is_dir()

    def _meta_path(self, universe_id: str) -> Path:
        return self._root / _META / f"{universe_id}.json"

    # --- git helper ---

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self._repo),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    # --- lifecycle ---

    def branch_code_variant(self, universe_id: str, *, start_ref: str = "HEAD") -> str:
        """从 start_ref 拉出新分支 + worktree，返回分支名（即 code_ref）。"""
        wt = self.worktree_dir(universe_id)
        if wt.exists():
            raise FileExistsError(f"worktree already exists: {wt}")
        branch = self.branch_name(universe_id)
        start_sha = self._git("rev-parse", "--verify", start_ref).strip()
        self._git("worktree", "add", "-b", branch, str(wt), start_sha)
        self._meta_path(universe_id).write_text(
            json.dumps({"branch": branch, "start_ref": start_sha}, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Code variant worktree created: %s @ %s", branch, start_sha[:8])
        return branch

    def _read_meta(self, universe_id: str) -> dict:
        p = self._meta_path(universe_id)
        if not p.exists():
            raise FileNotFoundError(f"code variant meta missing: {universe_id}")
        return json.loads(p.read_text(encoding="utf-8"))
