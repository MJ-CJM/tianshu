"""EvalHarness — 回放历史 goal 到沙箱变体并按 compute_fitness 打分。

纯逻辑（选集/打分/聚合）可单测；evaluate() 是活路径（起沙箱 + HTTP 提交 + 轮询），需 live 验证。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from tianshu.universe.fitness import compute_fitness

logger = logging.getLogger(__name__)

# 终态状态集：memorial.status 进入这些值后任务结束
_TERMINAL_STATUSES = {"completed", "approved", "failed", "rejected"}


class EvalHarness:
    def __init__(
        self,
        storage,
        sandbox_runner,
        *,
        fitness_weights=(0.4, 0.15, 0.2, 0.1, 0.15),
        base_env: dict[str, str] | None = None,
    ):
        self._storage = storage
        self._sandbox = sandbox_runner
        self._weights = fitness_weights
        self._base_env = dict(base_env or {})

    def select_eval_set(self, size: int) -> list[str]:
        """分层选集:约 60% 最近成功 + 40% 最近失败(跨层去重,不足互补)。

        含失败样本让变体有机会证明「修好了冠军跑不动的」——配对基线下
        冠军在失败样本上同样失败,变体修好即体现为正 delta。

        edict 没有 failed 生命周期(失败挂在 memorial 层，见 _collect_failed_goals)。
        """
        n_fail = int(size * 0.4)
        fail_goals = self._collect_failed_goals(n_fail)
        succ_goals = self._collect_goals(
            "completed", size - len(fail_goals), exclude=set(fail_goals)
        )
        short = size - len(succ_goals) - len(fail_goals)
        if short > 0:
            fail_goals.extend(
                self._collect_failed_goals(short, exclude=set(fail_goals) | set(succ_goals))
            )
        return succ_goals + fail_goals

    def _collect_goals(self, status: str, want: int, exclude: set[str] | None = None) -> list[str]:
        """从指定状态收集不重复的 goal，最多 want 条。

        exclude: 要跳过的 goal 集合（跨层去重）。
        """
        if want <= 0:
            return []
        seen: set[str] = set(exclude or ())
        edicts, _ = self._storage.list_edicts(status=status, limit=want * 3)
        goals: list[str] = []
        for e in edicts:
            g = e.goal.strip()
            if g and g not in seen:
                seen.add(g)
                goals.append(g)
            if len(goals) >= want:
                break
        return goals

    def _collect_failed_goals(self, want: int, exclude: set[str] | None = None) -> list[str]:
        """从 memorial.status=failed 反查 edict.goal 收集失败样本，最多 want 条。

        edict 本身没有 failed 状态——失败是 memorial 层的终态，因此不能像
        _collect_goals 那样直接 list_edicts(status="failed")（永远查不到）。

        exclude: 要跳过的 goal 集合（跨层去重）。
        """
        if want <= 0:
            return []
        seen: set[str] = set(exclude or ())
        mems, _ = self._storage.list_memorials(status="failed", limit=want * 3)
        goals: list[str] = []
        for m in mems:
            edict = self._storage.get_edict(m.edict_id)
            if edict is None:
                continue
            g = edict.goal.strip()
            if g and g not in seen:
                seen.add(g)
                goals.append(g)
            if len(goals) >= want:
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

    def collect_goal_results(self, db_path: Path) -> list[dict]:
        """沙箱 DB 的 per-goal 明细(instruction/status/failure_reason/cost)。

        平台级评测报告用(失败归因分布、逐条结果表)。SELECT * + keys 判断
        以兼容旧代码变体的沙箱 DB(可能没有 failure_reason 列);缺表/坏库
        宽容返回空(评估失败安全:per-goal 明细是增值信息,不阻断打分)。
        """
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM memorials ORDER BY created_at ASC").fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("eval: goal results unavailable from %s: %s", db_path, e)
            return []
        finally:
            conn.close()
        results: list[dict] = []
        for r in rows:
            keys = r.keys()
            cost = 0.0
            with contextlib.suppress(ValueError, TypeError):
                cost = float(json.loads(r["usage_json"] or "{}").get("cost_cny", 0.0) or 0.0)
            results.append(
                {
                    "instruction": r["instruction"] if "instruction" in keys else None,
                    "status": r["status"],
                    "error": r["error"],
                    "failure_reason": (r["failure_reason"] if "failure_reason" in keys else None),
                    "cost": round(cost, 6),
                }
            )
        return results

    def evaluate(
        self,
        worktree: Path,
        *,
        eval_set: list[str],
        seed_db: Path | None = None,
        goal_timeout_s: int = 300,
        extra_env: dict | None = None,
        budget_cny: float | None = None,
    ) -> dict:
        """Compatibility wrapper for offline CLI callers outside an event loop."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.evaluate_async(
                    worktree,
                    eval_set=eval_set,
                    seed_db=seed_db,
                    goal_timeout_s=goal_timeout_s,
                    extra_env=extra_env,
                    budget_cny=budget_cny,
                )
            )
        raise RuntimeError(
            "EvalHarness.evaluate() cannot run in an event loop; await evaluate_async()"
        )

    async def evaluate_async(
        self,
        worktree: Path,
        *,
        eval_set: list[str],
        seed_db: Path | None = None,
        goal_timeout_s: int = 300,
        extra_env: dict | None = None,
        budget_cny: float | None = None,
    ) -> dict:
        """在沙箱中回放 eval_set，聚合并打分。

        iso_db 放在 worktree 同级目录（保持 worktree 内文件系统干净），文件名唯一化
        （随机后缀），避免并发评估互踩。
        seed_db 不为 None 时拷贝作为初始数据库（例如携带 persona/LLM 配置）。
        budget_cny 非 None 时逐条回放后检查沙箱 DB 累计成本,触顶即截断
        (truncated=True),已回放部分照常聚合打分——评估必须失败安全,
        预算闸只截断、不作废。
        """
        iso_db = Path(worktree).parent / f"_eval-{uuid.uuid4().hex[:8]}.db"
        if seed_db is not None:
            shutil.copy(seed_db, iso_db)
        merged_env = {**self._base_env, **(extra_env or {})} or None
        session = self._sandbox.session(worktree, db_path=iso_db, extra_env=merged_env)
        if hasattr(session, "__aenter__"):
            async with session as handle:
                return await self._evaluate_session(
                    handle,
                    eval_set=eval_set,
                    goal_timeout_s=goal_timeout_s,
                    budget_cny=budget_cny,
                )
        with session as handle:
            return await self._evaluate_session(
                handle,
                eval_set=eval_set,
                goal_timeout_s=goal_timeout_s,
                budget_cny=budget_cny,
            )

    async def _evaluate_session(
        self,
        h,
        *,
        eval_set: list[str],
        goal_timeout_s: int,
        budget_cny: float | None,
    ) -> dict:
        truncated = False
        ran = 0
        for goal in eval_set:
            await asyncio.to_thread(self._run_goal, h.base_url, goal, goal_timeout_s)
            ran += 1
            if (
                budget_cny is not None
                and (await asyncio.to_thread(self.aggregate_db_stats, h.db_path))["cost"]
                >= budget_cny
            ):
                truncated = True
                logger.warning(
                    "eval: budget %.2f CNY reached after %d/%d goals, truncating",
                    budget_cny,
                    ran,
                    len(eval_set),
                )
                break
        stats = await asyncio.to_thread(self.aggregate_db_stats, h.db_path)
        goal_results = await asyncio.to_thread(self.collect_goal_results, h.db_path)
        return {
            "fitness": self.score(stats),
            "stats": stats,
            "n": ran,
            "truncated": truncated,
            "goal_results": goal_results,
        }

    @staticmethod
    def eval_set_fingerprint(eval_set: list[str], champion_key: str) -> str:
        """评估集指纹:内容 + 冠军标识。冠军更替或选集内容变化都会使基线缓存失效。"""
        payload = "\n".join(eval_set) + "|" + champion_key
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def evaluate_paired(
        self,
        variant_worktree: Path,
        *,
        eval_set: list[str],
        baseline_worktree: Path,
        variant_env: dict | None = None,
        baseline_env: dict | None = None,
        goal_timeout_s: int = 300,
        budget_cny: float | None = None,
        cached_baseline: dict | None = None,
    ) -> dict:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.evaluate_paired_async(
                    variant_worktree,
                    eval_set=eval_set,
                    baseline_worktree=baseline_worktree,
                    variant_env=variant_env,
                    baseline_env=baseline_env,
                    goal_timeout_s=goal_timeout_s,
                    budget_cny=budget_cny,
                    cached_baseline=cached_baseline,
                )
            )
        raise RuntimeError(
            "EvalHarness.evaluate_paired() cannot run in an event loop; "
            "await evaluate_paired_async()"
        )

    async def evaluate_paired_async(
        self,
        variant_worktree: Path,
        *,
        eval_set: list[str],
        baseline_worktree: Path,
        variant_env: dict | None = None,
        baseline_env: dict | None = None,
        goal_timeout_s: int = 300,
        budget_cny: float | None = None,
        cached_baseline: dict | None = None,
    ) -> dict:
        """变体与冠军基线在同一评估集上各评一次,margin 判在配对差上。

        cached_baseline(同指纹的历史基线)命中时跳过基线评估,评估成本减半。
        """
        variant = await self.evaluate_async(
            variant_worktree,
            eval_set=eval_set,
            extra_env=variant_env,
            goal_timeout_s=goal_timeout_s,
            budget_cny=budget_cny,
        )
        baseline: dict[str, Any]
        if cached_baseline is not None:
            baseline = (
                cached_baseline
                if "fitness" in cached_baseline
                else {"fitness": cached_baseline, "stats": {}, "n": 0, "truncated": False}
            )
            baseline_cached = True
        else:
            baseline = await self.evaluate_async(
                baseline_worktree,
                eval_set=eval_set,
                extra_env=baseline_env,
                goal_timeout_s=goal_timeout_s,
                budget_cny=budget_cny,
            )
            baseline_cached = False
        delta = round(
            variant["fitness"].get("score", 0.0) - baseline["fitness"].get("score", 0.0), 4
        )
        return {
            "variant": variant,
            "baseline": baseline,
            "delta": delta,
            "baseline_cached": baseline_cached,
        }

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
            edict_id,
            goal[:60],
            timeout_s,
        )
