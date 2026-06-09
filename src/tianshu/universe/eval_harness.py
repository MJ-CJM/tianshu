"""EvalHarness — 回放历史 goal 到沙箱变体并按 compute_fitness 打分。

纯逻辑（选集/打分/聚合）可单测；evaluate() 是活路径（起沙箱 + HTTP 提交 + 轮询），需 live 验证。
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

from tianshu.universe.fitness import compute_fitness

logger = logging.getLogger(__name__)

# 终态状态集：memorial.status 进入这些值后任务结束
_TERMINAL_STATUSES = {"completed", "approved", "failed", "rejected"}


class EvalHarness:
    def __init__(self, storage, sandbox_runner, *, fitness_weights=(0.4, 0.15, 0.2, 0.1, 0.15)):
        self._storage = storage
        self._sandbox = sandbox_runner
        self._weights = fitness_weights

    def select_eval_set(self, size: int) -> list[str]:
        """从历史已完成 edict 取代表性 goal 列表（最多 size 条，去重）。

        使用 storage.list_edicts(status='completed') 读取历史，按创建时间倒序取最近，去重。
        """
        edicts, _ = self._storage.list_edicts(status="completed", limit=size * 3)
        seen: set[str] = set()
        goals: list[str] = []
        for e in edicts:
            g = e.goal.strip()
            if g and g not in seen:
                seen.add(g)
                goals.append(g)
            if len(goals) >= size:
                break
        return goals

    def score(self, stats: dict) -> dict:
        """包装 compute_fitness，使用构造时指定的权重。"""
        return compute_fitness(stats, weights=self._weights)

    def aggregate_db_stats(self, db_path: Path) -> dict:
        """打开一个沙箱隔离 DB，聚合其全部 memorial 为 compute_fitness 所需 stats。

        与 storage.universe_memorial_stats() 同构：
          total / success / retries / audited / audit_pass / cost / feedback
        但不按 universe_id 过滤——沙箱 DB 只有本次 eval 的数据。
        """
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT status, attempt, usage_json, audit_json, feedback_score FROM memorials"
            ).fetchall()
        finally:
            conn.close()

        total = len(rows)
        success = sum(1 for r in rows if r["status"] in ("completed", "approved"))
        retries = sum(max(0, (r["attempt"] or 1) - 1) for r in rows)
        feedback = sum((r["feedback_score"] or 0) for r in rows)
        audited = 0
        audit_pass = 0
        cost = 0.0
        for r in rows:
            try:
                u = json.loads(r["usage_json"] or "{}")
                cost += float(u.get("cost_cny", 0.0) or 0.0)
            except (ValueError, TypeError):
                pass
            aj = r["audit_json"]
            if aj:
                audited += 1
                try:
                    a = json.loads(aj)
                    if a.get("verdict") == "pass":
                        audit_pass += 1
                except (ValueError, TypeError):
                    pass
        return {
            "total": total,
            "success": success,
            "retries": retries,
            "audited": audited,
            "audit_pass": audit_pass,
            "cost": round(cost, 6),
            "feedback": feedback,
        }

    def evaluate(
        self,
        worktree: Path,
        *,
        eval_set: list[str],
        seed_db: Path | None = None,
        goal_timeout_s: int = 300,
    ) -> dict:
        """在沙箱中回放 eval_set，聚合并打分。

        iso_db 放在 worktree 同级目录（保持 worktree 内文件系统干净）。
        seed_db 不为 None 时拷贝作为初始数据库（例如携带 persona/LLM 配置）。
        """
        iso_db = Path(worktree).parent / "_eval.db"
        if seed_db is not None:
            shutil.copy(seed_db, iso_db)
        with self._sandbox.session(worktree, db_path=iso_db) as h:
            for goal in eval_set:
                self._run_goal(h.base_url, goal, goal_timeout_s)
            stats = self.aggregate_db_stats(h.db_path)
        return {"fitness": self.score(stats), "stats": stats, "n": len(eval_set)}

    def _run_goal(self, base_url: str, goal: str, timeout_s: int) -> None:
        """POST 一个 edict 并轮询其 memorial 到终态（或超时放弃）。

        POST /api/edicts  {"goal": goal}  → data.id
        GET  /api/edicts/{id}/memorial    → data.status
        终态：completed / approved / failed / rejected
        超时时记录 warning，不抛异常（eval 继续）。
        """
        # --- 提交 edict ---
        payload = json.dumps({"goal": goal}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/edicts",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                body = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            logger.warning("eval: failed to submit goal %r: %s", goal[:60], exc)
            return

        edict_id = (body.get("data") or {}).get("id")
        if not edict_id:
            logger.warning("eval: no edict id in response for goal %r", goal[:60])
            return

        # --- 轮询 memorial 到终态 ---
        memorial_url = f"{base_url}/api/edicts/{edict_id}/memorial"
        deadline = time.monotonic() + timeout_s
        poll_interval = 2.0
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            try:
                with urllib.request.urlopen(memorial_url, timeout=10) as resp:  # noqa: S310
                    result = json.loads(resp.read())
            except Exception as exc:  # noqa: BLE001
                logger.debug("eval: poll error for edict %s: %s", edict_id, exc)
                continue
            data = result.get("data") or {}
            status = data.get("status")
            if status in _TERMINAL_STATUSES:
                logger.debug("eval: edict %s reached status %s", edict_id, status)
                return
        logger.warning(
            "eval: timeout waiting for edict %s (goal=%r) after %ss",
            edict_id, goal[:60], timeout_s,
        )
