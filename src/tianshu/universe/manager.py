"""位面管理：genesis / branch / switch / rollback / diff / archive。"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tianshu.universe.model import Universe, UniverseOrigin, UniverseStatus
from tianshu.universe.router import ChallengerRouter
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
        deployer: Any | None = None,
        challenger_router: ChallengerRouter | None = None,
    ) -> None:
        self._storage = storage
        self._store = store
        self._personas = persona_loader
        self._skills = skills_loader
        self._config_snapshot = config_snapshot  # () -> dict
        self._config_apply = config_apply  # (dict) -> None
        self._bus = event_bus
        self._agent_config = agent_config or (lambda: None)
        self._code_store = code_store
        self._deployer = deployer
        self._challenger_router = challenger_router

    def attach_event_bus(self, bus: Any) -> None:
        self._bus = bus

    # --- queries ---

    def champion(self) -> dict | None:
        return self._storage.get_champion_universe()

    def champion_id(self) -> str | None:
        champ = self.champion()
        return champ["id"] if champ else None

    def route_for_memorial(self, memorial_id: str) -> str | None:
        """Return only the legacy Universe projection for an already assigned run."""
        if self._challenger_router is None:
            raise RuntimeError("challenger_router_required")
        if self._challenger_router.overlay_for(memorial_id) is None:
            raise LookupError("run assignment not found")
        return self.champion_id()

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
        self._emit(
            "universe.created",
            {
                "universe_id": child.id,
                "parent": parent_id,
                "origin": origin.value,
            },
        )
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
        self._emit(
            "universe.created",
            {
                "universe_id": child.id,
                "parent": parent_id,
                "origin": UniverseOrigin.CODE_VARIANT.value,
                "code_ref": code_ref,
            },
        )
        return self._storage.get_universe(child.id)

    # --- promote code variant ---

    def promote_code_variant(self, universe_id: str) -> dict:
        """Reject the retired unauthenticated promotion mutation boundary."""
        del universe_id
        raise RuntimeError("promotion_service_required")

    # --- switch / rollback ---

    def switch(self, universe_id: str) -> dict:
        """Reject the retired unauthenticated live-universe mutation boundary."""
        del universe_id
        raise RuntimeError("promotion_service_required")

    def rollback(self, universe_id: str) -> dict:
        """Reject legacy rollback; governed rollback requires PromotionService."""
        del universe_id
        raise RuntimeError("promotion_service_required")

    # --- delete ---

    def delete(self, universe_id: str) -> dict:
        """彻底删除一个位面（不可恢复）。冠军不可删（先切走）。返回 {"id": ...}。"""
        target = self._storage.get_universe(universe_id)
        if not target:
            raise ValueError(f"universe not found: {universe_id}")
        if target["status"] == UniverseStatus.CHAMPION.value:
            raise ValueError("cannot delete the champion (switch away first)")
        if target.get("code_ref") and self._code_store:
            self._code_store.remove(universe_id)
        else:
            self._store.remove(universe_id)
        self._storage.delete_universe(universe_id)
        self._emit("universe.deleted", {"universe_id": universe_id})
        return {"id": universe_id}

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
            "skills": self._diff_dir(self._store.skills_dir(a_id), self._store.skills_dir(b_id)),
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

        self._bus.fire(
            make_event(
                event_type=event_type,
                edict_id=None,
                memorial_id=None,
                producer="universe_manager",
                payload=payload,
            )
        )
