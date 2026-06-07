"""位面行为快照的落盘与还原（全量拷贝，v1 简单安全）。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_PERSONAS = "personas"
_SKILLS = "skills"
_MANIFEST = "manifest.json"


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
        self._copy_tree(self._live_personas, dst / _PERSONAS)
        self._copy_tree(self._live_skills, dst / _SKILLS)
        self.write_manifest(universe_id, config_snapshot)

    def branch_from(self, parent_id: str, child_id: str) -> None:
        """从父位面目录全量拷贝出子位面目录（含 manifest）。"""
        src = self.universe_dir(parent_id)
        if not (src / _MANIFEST).exists():
            raise FileNotFoundError(f"parent universe dir missing: {src}")
        dst = self.universe_dir(child_id)
        if dst.exists():
            raise FileExistsError(f"universe dir already exists: {dst}")
        shutil.copytree(src, dst)

    def restore_to_live(self, universe_id: str) -> dict:
        """把某位面目录还原到 live runtime（覆盖 personas/ skills/），返回其 config 快照。"""
        src = self.universe_dir(universe_id)
        if not (src / _MANIFEST).exists():
            raise FileNotFoundError(f"universe dir missing: {src}")
        self._copy_tree(src / _PERSONAS, self._live_personas)
        self._copy_tree(src / _SKILLS, self._live_skills)
        return self.read_manifest(universe_id)

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
    def _copy_tree(src: Path, dst: Path) -> None:
        """全量替换 dst 为 src 的内容（src 缺失则清空 dst）。"""
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)
