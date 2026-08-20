"""位面行为快照的落盘与还原。

写入位面目录时，只读二进制资产走硬链接、其余文件真实拷贝——单份快照因此从
9.5M 降到约 2M（skills 里 84% 是永不变异的字体与 OOXML schema）。

**为什么不能对全部文件用硬链接**：``Path.write_text`` 是 ``open('w')`` 语义，
原地截断同一个 inode。而 skill_curator 会改写 SKILL.md、mutator 会改写
SOUL.md / ROLE.md——这些文本若共享 inode，一次改写就会串改所有位面，
直接击穿位面隔离。只读资产没有任何写入路径，对它们硬链接才是安全的。

还原方向（``restore_to_live``）一律真实拷贝：live 目录会被人和系统随手改动，
不能与任何位面共享 inode。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_PERSONAS = "personas"
_SKILLS = "skills"
_MANIFEST = "manifest.json"

# 只读二进制资产：字体、OOXML schema、随技能附带的样例文档。
# 判据是"代码里没有任何写入路径"，不是"看起来像二进制"——新增类型前先确认这一点。
_LINKABLE_SUFFIXES = frozenset({".ttf", ".xsd", ".pdf", ".gz"})


def _link_or_copy(src: str, dst: str) -> None:
    """只读资产共享 inode；其余（以及跨文件系统时）真实拷贝。"""
    if Path(src).suffix.lower() in _LINKABLE_SUFFIXES:
        try:
            os.link(src, dst)
            return
        except OSError:
            # 跨文件系统、超出链接数上限等：退回拷贝，语义不变只是占地大些。
            logger.debug("hardlink failed, falling back to copy: %s", src, exc_info=True)
    shutil.copy2(src, dst)


class UniverseStore:
    """每个位面落在 ``{root}/{universe_id}/``：personas/ + skills/ + manifest.json。

    personas/ 与 skills/ 是当前 runtime 行为目录的全量拷贝；
    manifest.json 存 config 类快照（agent/LLM/providers/policy 等，JSON 可序列化）。
    """

    def __init__(
        self,
        root: Path,
        live_personas_dir: Path,
        live_skills_dir: Path,
    ) -> None:
        self._root = Path(root).expanduser()
        self._live_personas = Path(live_personas_dir).expanduser()
        self._live_skills = Path(live_skills_dir).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def universe_dir(self, universe_id: str) -> Path:
        return self._root / universe_id

    def personas_dir(self, universe_id: str) -> Path:
        return self.universe_dir(universe_id) / _PERSONAS

    def skills_dir(self, universe_id: str) -> Path:
        return self.universe_dir(universe_id) / _SKILLS

    def exists(self, universe_id: str) -> bool:
        return (self.universe_dir(universe_id) / _MANIFEST).exists()

    def snapshot_live(self, universe_id: str, config_snapshot: dict) -> None:
        """把当前 live runtime 行为目录 + config 快照拷入新位面目录。"""
        dst = self.universe_dir(universe_id)
        dst.mkdir(parents=True, exist_ok=True)
        self._copy_tree(self._live_personas, dst / _PERSONAS, link_readonly=True)
        self._copy_tree(self._live_skills, dst / _SKILLS, link_readonly=True)
        self.write_manifest(universe_id, config_snapshot)

    def branch_from(self, parent_id: str, child_id: str) -> None:
        """从父位面目录全量拷贝出子位面目录（含 manifest）。"""
        src = self.universe_dir(parent_id)
        if not (src / _MANIFEST).exists():
            raise FileNotFoundError(f"parent universe dir missing: {src}")
        dst = self.universe_dir(child_id)
        if dst.exists():
            raise FileExistsError(f"universe dir already exists: {dst}")
        shutil.copytree(src, dst, copy_function=_link_or_copy)

    def restore_to_live(self, universe_id: str) -> dict:
        """把某位面目录还原到 live runtime（覆盖 personas/ skills/），返回其 config 快照。"""
        src = self.universe_dir(universe_id)
        if not (src / _MANIFEST).exists():
            raise FileNotFoundError(f"universe dir missing: {src}")
        self._copy_tree(src / _PERSONAS, self._live_personas)
        self._copy_tree(src / _SKILLS, self._live_skills)
        return self.read_manifest(universe_id)

    def remove(self, universe_id: str) -> None:
        """删除该位面的落盘目录（personas/skills/manifest）。不存在则忽略。"""
        d = self.universe_dir(universe_id)
        if d.exists():
            shutil.rmtree(d)

    def write_manifest(self, universe_id: str, config_snapshot: dict) -> None:
        path = self.universe_dir(universe_id) / _MANIFEST
        path.write_text(
            json.dumps(config_snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read_manifest(self, universe_id: str) -> dict:
        path = self.universe_dir(universe_id) / _MANIFEST
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _copy_tree(src: Path, dst: Path, *, link_readonly: bool = False) -> None:
        """全量替换 dst 为 src 的内容（src 缺失则清空 dst）。

        ``link_readonly`` 仅在写入位面目录时开启；还原到 live 必须保持默认的真实拷贝。
        """
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            copy_function = _link_or_copy if link_readonly else shutil.copy2
            shutil.copytree(src, dst, copy_function=copy_function)
        else:
            dst.mkdir(parents=True, exist_ok=True)
