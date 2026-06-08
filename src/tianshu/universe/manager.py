"""位面管理：genesis / branch / switch / rollback / diff / archive。"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable

from tianshu.universe.model import Universe, UniverseOrigin, UniverseStatus
from tianshu.universe.store import UniverseStore

logger = logging.getLogger(__name__)


class UniverseManager:
    def __init__(
        self,
        storage: Any,
        store: UniverseStore,
        persona_loader: Any,
        skills_loader: Any,
        config_snapshot: Callable[[], dict],
        config_apply: Callable[[dict], None],
        event_bus: Any | None = None,
        agent_config: Callable[[], Any] | None = None,
        code_store: Any | None = None,
    ) -> None:
        self._storage = storage
        self._store = store
        self._personas = persona_loader
        self._skills = skills_loader
        self._config_snapshot = config_snapshot  # () -> dict
        self._config_apply = config_apply        # (dict) -> None
        self._bus = event_bus
        self._agent_config = agent_config or (lambda: None)
        self._code_store = code_store

    def attach_event_bus(self, bus: Any) -> None:
        self._bus = bus

    # --- queries ---

    def champion(self) -> dict | None:
        return self._storage.get_champion_universe()

    def champion_id(self) -> str | None:
        champ = self.champion()
        return champ["id"] if champ else None

    def route_for_memorial(self, memorial_id: str) -> str | None:
        """返回本 memorial 应归属的位面：默认冠军；按 explore_ratio 概率分给在线候选。

        用 memorial_id 的稳定哈希做确定性分桶（无随机源、可复现；ULID 安全）。
        """
        champ_id = self.champion_id()
        cfg = self._agent_config()
        if not getattr(cfg, "parallel_universe_enabled", False):
            return champ_id
        ratio = getattr(cfg, "universe_explore_ratio", 0.1)
        if ratio <= 0:
            return champ_id
        challengers = [
            u for u in self.list(include_archived=False)
            if u["status"] == "challenger"
        ]
        if not challengers:
            return champ_id
        h = int(hashlib.sha256((memorial_id or "").encode()).hexdigest(), 16)
        if (h % 100) < int(ratio * 100):
            idx = (h // 100) % len(challengers)
            return challengers[idx]["id"]
        return champ_id

    def list(self, *, include_archived: bool = True) -> list[dict]:
        return self._storage.list_universes(include_archived=include_archived)

    # --- genesis ---

    def ensure_genesis(self) -> dict:
        """首次启用：把当前运行态捕获为 genesis 位面并设为冠军；已存在则原样返回。"""
        champ = self.champion()
        if champ:
            return champ
        uni = Universe(
            name="创世位面",
            status=UniverseStatus.CHAMPION,
            origin=UniverseOrigin.GENESIS,
            description="首次启用平行位面时捕获的初始行为配置",
        )
        self._store.snapshot_live(uni.id, self._config_snapshot())
        self._storage.save_universe(uni.to_row())
        logger.info("Genesis universe created: %s", uni.id)
        self._emit("universe.created", {"universe_id": uni.id, "origin": "genesis"})
        return self._storage.get_universe(uni.id)

    # --- branch ---

    def branch(
        self,
        parent_id: str,
        name: str,
        *,
        origin: UniverseOrigin = UniverseOrigin.MANUAL_BRANCH,
        mutation_reason: str | None = None,
        description: str = "",
    ) -> dict:
        parent = self._storage.get_universe(parent_id)
        if not parent:
            raise ValueError(f"parent universe not found: {parent_id}")
        self._persist_champion()  # 若父=冠军，先把 live 漂移落进父目录
        child = Universe(
            name=name,
            status=UniverseStatus.CHALLENGER,
            origin=origin,
            parent_universe_id=parent_id,
            mutation_reason=mutation_reason,
            description=description,
        )
        self._store.branch_from(parent_id, child.id)
        self._storage.save_universe(child.to_row())
        self._emit("universe.created", {
            "universe_id": child.id, "parent": parent_id, "origin": origin.value,
        })
        return self._storage.get_universe(child.id)

    # --- code variant branch ---

    def branch_code_variant(
        self,
        parent_id: str,
        name: str,
        *,
        start_ref: str = "HEAD",
        description: str = "",
    ) -> dict:
        """从父位面分出一份代码变体（git worktree + 分支）；不复制行为配置数据层。"""
        if not self._code_store:
            raise RuntimeError("code variant store not configured")
        parent = self._storage.get_universe(parent_id)
        if not parent:
            raise ValueError(f"parent universe not found: {parent_id}")
        child = Universe(
            name=name,
            status=UniverseStatus.CHALLENGER,
            origin=UniverseOrigin.CODE_VARIANT,
            parent_universe_id=parent_id,
            description=description,
        )
        code_ref = self._code_store.branch_code_variant(child.id, start_ref=start_ref)
        child = dataclasses.replace(child, code_ref=code_ref)
        self._storage.save_universe(child.to_row())
        self._emit("universe.created", {
            "universe_id": child.id, "parent": parent_id,
            "origin": UniverseOrigin.CODE_VARIANT.value, "code_ref": code_ref,
        })
        return self._storage.get_universe(child.id)

    # --- switch / rollback ---

    def switch(self, universe_id: str) -> dict:
        """把目标位面置为冠军、原冠军降级，并重定向运行态。"""
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if target.get("code_ref"):
            raise ValueError(
                "code variant switch/promotion requires the Deployer "
                "(Phase 2 increment 2d)"
            )
        if target["status"] == UniverseStatus.ARCHIVED.value:
            raise ValueError("cannot switch to an archived universe")
        if not self._store.exists(universe_id):
            raise FileNotFoundError(f"universe dir missing: {universe_id}")

        prev = self.champion()
        if prev and prev["id"] == universe_id:
            return target  # 已是冠军，no-op

        # 切走前持久化原冠军的 live 漂移（git 式：冠军是工作副本）
        if prev:
            self._store.snapshot_live(prev["id"], self._config_snapshot())

        # 还原运行态目录 + 取回 config 快照
        manifest = self._store.restore_to_live(universe_id)
        # live 目录路径固定；restore_to_live 已就地覆盖其内容，
        # 这两行让 loader 清缓存并从磁盘重新加载新内容。
        self._personas.repoint_runtime(self._personas.runtime_dir)
        self._skills.repoint_user_dir(self._skills_user_dir())
        self._config_apply(manifest)

        # 翻转状态（先降原冠军，避免 partial-unique 冲突）
        if prev:
            self._storage.set_universe_status(prev["id"], UniverseStatus.CHALLENGER.value)
        self._storage.set_universe_status(universe_id, UniverseStatus.CHAMPION.value)
        self._emit("universe.switched", {
            "from": prev["id"] if prev else None, "to": universe_id,
        })
        return self._storage.get_universe(universe_id)

    # rollback 语义等同 switch 到历史位面
    rollback = switch

    # --- archive ---

    def archive(self, universe_id: str) -> dict:
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if target["status"] == UniverseStatus.CHAMPION.value:
            raise ValueError("cannot archive the champion (switch away first)")
        self._storage.set_universe_status(universe_id, UniverseStatus.ARCHIVED.value)
        if target.get("code_ref") and self._code_store:
            self._code_store.gc_worktree(universe_id)
        self._emit("universe.archived", {"universe_id": universe_id})
        return self._storage.get_universe(universe_id)

    # --- restore ---

    def restore(self, universe_id: str) -> dict:
        """把已归档位面恢复为候选位面（archived → challenger）。"""
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if target["status"] != UniverseStatus.ARCHIVED.value:
            raise ValueError("only archived universes can be restored")
        self._storage.set_universe_status(universe_id, UniverseStatus.CHALLENGER.value)
        if target.get("code_ref") and self._code_store:
            self._code_store.restore_worktree(universe_id)
        self._emit("universe.restored", {"universe_id": universe_id})
        return self._storage.get_universe(universe_id)

    # --- diff ---

    def diff(self, a_id: str, b_id: str) -> dict:
        """对比两位面行为配置：人格文本、技能集、config 快照。"""
        self._persist_champion()  # 冠军是工作副本，对比前先回写 live 漂移
        return {
            "personas": self._diff_dir(
                self._store.personas_dir(a_id), self._store.personas_dir(b_id)
            ),
            "skills": self._diff_dir(
                self._store.skills_dir(a_id), self._store.skills_dir(b_id)
            ),
            "config": self._diff_config(
                self._store.read_manifest(a_id), self._store.read_manifest(b_id)
            ),
        }

    def code_diff(self, universe_id: str) -> str:
        """返回代码变体位面相对其 fork 起点的 git diff。"""
        if not self._code_store:
            raise RuntimeError("code variant store not configured")
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if not target.get("code_ref"):
            raise ValueError("not a code variant universe")
        return self._code_store.diff(universe_id)

    @staticmethod
    def _diff_dir(a: Path, b: Path) -> dict:
        def files(root: Path) -> dict[str, str]:
            out: dict[str, str] = {}
            if root.exists():
                for f in sorted(root.rglob("*")):
                    if f.is_file():
                        out[str(f.relative_to(root))] = f.read_text(
                            encoding="utf-8", errors="replace"
                        )
            return out
        fa, fb = files(a), files(b)
        keys = set(fa) | set(fb)
        return {
            "only_in_a": sorted(set(fa) - set(fb)),
            "only_in_b": sorted(set(fb) - set(fa)),
            "changed": sorted(k for k in keys if k in fa and k in fb and fa[k] != fb[k]),
        }

    @staticmethod
    def _diff_config(a: dict, b: dict) -> dict:
        keys = set(a) | set(b)
        return {k: {"a": a.get(k), "b": b.get(k)} for k in sorted(keys) if a.get(k) != b.get(k)}

    # --- helpers ---

    def _persist_champion(self) -> None:
        """把在役冠军的 live 漂移回写进其存盘目录（使其存盘快照与 live 一致）。"""
        champ = self.champion()
        if champ:
            self._store.snapshot_live(champ["id"], self._config_snapshot())

    def _skills_user_dir(self) -> Path:
        return Path(self._skills.user_dir)

    def _emit(self, event_type: str, payload: dict) -> None:
        if not self._bus:
            return
        from tianshu.models.events import make_event
        self._bus.fire(make_event(
            event_type=event_type, edict_id=None, memorial_id=None,
            producer="universe_manager", payload=payload,
        ))
