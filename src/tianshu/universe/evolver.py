"""UniverseEvolver（演化）— 从冠军位面变异出候选并据适应度择优。

骨架对齐 SkillCurator：gate(idle + lock) → 采信号 → ONE LLM 变异 →
分支候选位面 → 沙箱配对评估 delta 分流(劣汰归档/达标推荐/带内留观) → 晋升推荐（默认人工确认）。

变异落地（mutator）：分支候选后，调 universe.mutator.apply_mutation 把变异意图真正
改写进候选位面文件。当前 cut 仅支持【人格文件】(SOUL.md / ROLE.md) 改写——人格目录被
位面完整快照、切换时 restore+reload，零改造即端到端生效。变异提示也已收窄为只提人格 target。

§限制（待后续）：policy / config / skillset 类变异尚未落地——它们需要先扩展位面快照范围
（把 session_rules、完整 config、技能状态纳入 snapshot/restore）。在此之前这些 target 走
"只记录不改写"兜底（mutation_applied=False）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from tianshu.universe.model import UniverseOrigin, UniverseStatus

logger = logging.getLogger(__name__)

_LOCK_KEY = "__universe_evolver__"
_CODE_LOCK_KEY = "__universe_code_propose__"

_SYSTEM = (
    "你是天枢的「演化」官，负责让宫殿的行为配置随使用越来越贴合主上。"
    "给定冠军位面的行为概要与各位面适应度，你只提出【一处】定向变异，"
    "用以分支出一个候选位面去试验。严禁臆造，只输出 JSON 对象，不带 markdown 代码块标记。"
)

_USER = """\
冠军位面适应度：{champion_fitness}
各候选位面适应度：{challenger_fitness}
冠军行为概要（可改写的官员人格）：
{summary}

主上画像（起居注蒸馏，变异方向应贴合其偏好；无则忽略）：
{profile}

近期已尝试过的变异(含结局,请勿重复相同或相近方向):
{history}

请提出【一处】可能让宫殿更贴合主上的人格改写，输出 JSON：
{{"target": "persona:<官员id>/ROLE.md 或 persona:<官员id>/SOUL.md（官员id 必须取自上面列出的官员）",
  "reason": "改这个文件的哪一点、为何可能更贴合(不得与已尝试方向重复)",
  "name": "候选位面名称（简短中文）"}}
若当前无明确可改之处，输出 {{"target": null, "reason": "...", "name": null}}。"""


@dataclass
class EvolveResult:
    skipped: str | None = None
    created_challenger: str | None = None
    mutation_reason: str | None = None
    mutation_applied: bool = False
    mutation_detail: str | None = None
    retired: list[str] = field(default_factory=list)
    promotion_recommended: str | None = None
    eval_delta: float | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skipped": self.skipped,
            "created_challenger": self.created_challenger,
            "mutation_reason": self.mutation_reason,
            "mutation_applied": self.mutation_applied,
            "mutation_detail": self.mutation_detail,
            "retired": self.retired,
            "promotion_recommended": self.promotion_recommended,
            "eval_delta": self.eval_delta,
            "errors": self.errors,
        }


class UniverseEvolver:
    def __init__(
        self,
        llm_client: Any,
        manager: Any,
        storage: Any,
        config_manager: Any,
        code_store: Any = None,
        gate: Any = None,
        sandbox: Any = None,
        eval_harness: Any = None,
        code_mutator: Any = None,
        diagnostician: Any = None,
        feature_flags: Any = None,
        consultation: Any = None,
        profile_provider: Any = None,
    ) -> None:
        self._llm = llm_client
        self._mgr = manager
        self._storage = storage
        self._config = config_manager
        self._bus: Any | None = None
        self._code_store = code_store
        self._gate = gate
        self._sandbox = sandbox
        self._eval_harness = eval_harness
        self._code_mutator = code_mutator
        self._diagnostician = diagnostician
        self._flags = feature_flags
        self._consultation = consultation
        self._profile_provider = profile_provider

    def attach_event_bus(self, bus: Any) -> None:
        self._bus = bus

    def _idle_ok(self, idle_hours: int) -> bool:
        from tianshu.skills.curator import _age_hours

        last = self._storage.last_activity_at()
        age = _age_hours(last)
        return age is None or age >= idle_hours

    def _mutation_history(self, limit: int = 20) -> str:
        """近期变异尝试台账(含已归档),供变异 prompt 避免重复方向。"""
        rows = [
            u
            for u in self._mgr.list(include_archived=True)
            if u.get("origin") == UniverseOrigin.MUTATION.value
        ]
        rows.sort(key=lambda u: u.get("created_at") or "", reverse=True)
        lines = []
        for u in rows[:limit]:
            f = u.get("fitness") or {}
            outcome = {
                "champion": "已晋升",
                "challenger": "留观中",
                "archived": "已淘汰",
            }.get(u["status"], u["status"])
            lines.append(
                f"- [{outcome}] {u.get('mutation_reason') or u['name']}"
                f" (score={f.get('score', 'n/a')})"
            )
        return "\n".join(lines) or "(无历史尝试)"

    def _register_promotion_flag(self, child_id: str, delta: float) -> str | None:
        """晋升灰度旋钮(迭代 6 feature-flag):默认关、rollout 0%,操作者据此灰度放量 /
        秒级回退(设 enabled=False)——已过评估门未全量的进化产物的安全阀。"""
        if self._flags is None:
            return None
        key = f"universe:promote:{child_id}"
        try:
            self._flags.set(
                key,
                enabled=False,
                rollout_pct=0,
                description=f"候选 {child_id} 晋升灰度(delta={delta:.3f})",
            )
            return key
        except Exception:  # noqa: BLE001
            logger.exception("[EVOLVER] register promotion flag failed")
            return None

    async def _deliberate_promotion(self, child: dict, delta: float) -> dict | None:
        """位面晋升廷议门(迭代 6,ADR-0008):晋升审批附多视角白话论证,而非只有 fitness 数字。
        廷议缺席(未装配)或失败时返回 None——失败安全,不阻断推荐。"""
        if self._consultation is None:
            return None
        try:
            resp = await self._consultation.start(self._build_promotion_consultation(child, delta))
            return {
                "synthesis": getattr(resp, "synthesis", None),
                "opinions": [
                    {"persona": o.persona_name, "stance": o.stance, "opinion": o.opinion}
                    for o in getattr(resp, "opinions", [])
                ],
            }
        except Exception:  # noqa: BLE001
            logger.exception("[EVOLVER] promotion deliberation failed")
            return None

    def _build_promotion_consultation(self, child: dict, delta: float) -> Any:
        from tianshu.consultation.models import ConsultationRequest

        return ConsultationRequest(
            topic=f"位面晋升评议:{child.get('name') or child['id']}",
            context=(
                f"候选位面相对冠军的评估提升 delta={delta:.3f};"
                f"变异理由:{child.get('mutation_reason') or '(无)'}。"
                "请从各自职能视角判断:是否应把该候选晋升为新冠军?"
            ),
        )

    async def _on_promotion_recommended(self, child: dict, delta: float, samples: int) -> dict:
        """推荐晋升(非自动):挂灰度旋钮(B) + 召廷议附白话论证(E),汇成事件载荷。"""
        payload: dict[str, Any] = {
            "universe_id": child["id"],
            "delta": delta,
            "samples": samples,
        }
        flag_key = self._register_promotion_flag(child["id"], delta)
        if flag_key:
            payload["flag_key"] = flag_key
        deliberation = await self._deliberate_promotion(child, delta)
        if deliberation:
            payload["deliberation"] = deliberation
        return payload

    async def run(self, trigger_source: str = "manual") -> EvolveResult:
        cfg = self._config.agent_config
        if not getattr(cfg, "parallel_universe_enabled", False):
            return EvolveResult(skipped="disabled")
        if trigger_source != "manual" and not self._idle_ok(
            getattr(cfg, "universe_evolver_idle_hours", 2)
        ):
            return EvolveResult(skipped="not_idle")
        if not self._storage.try_acquire_synthesis_lock(_LOCK_KEY):
            return EvolveResult(skipped="lock_held")

        result = EvolveResult()
        try:
            champ = self._mgr.champion()
            if not champ:
                return EvolveResult(skipped="no_champion")

            mutation = await self._propose_mutation(champ)
            if mutation and mutation.get("target"):
                child = self._mgr.branch(
                    champ["id"],
                    mutation.get("name") or "演化候选",
                    origin=UniverseOrigin.MUTATION,
                    mutation_reason=mutation.get("reason"),
                    description=f"target={mutation.get('target')}",
                )
                result.created_challenger = child["id"]
                result.mutation_reason = mutation.get("reason")
                from tianshu.universe.mutator import apply_mutation

                applied = await apply_mutation(
                    self._mgr._store,  # noqa: SLF001
                    child["id"],
                    mutation,
                    self._llm,
                )
                result.mutation_applied = bool(applied.get("applied"))
                result.mutation_detail = applied.get("detail")

                if result.mutation_applied:
                    paired = await self._evaluate_behavior_challenger(child["id"], cfg)
                    if paired is not None:
                        margin = getattr(cfg, "universe_promote_margin", 0.05)
                        min_samples = getattr(cfg, "universe_min_samples", 20)
                        result.eval_delta = paired["delta"]
                        samples = paired["variant"]["fitness"].get("samples", 0)
                        if paired["delta"] <= -margin:
                            self._mgr.archive(child["id"])
                            result.retired.append(child["id"])
                        elif paired["delta"] >= margin and samples >= min_samples:
                            result.promotion_recommended = child["id"]
                            if getattr(cfg, "universe_auto_promote", False):
                                self._mgr.switch(child["id"])
                                await self._emit(
                                    "universe.promoted",
                                    {
                                        "universe_id": child["id"],
                                        "auto": True,
                                        "delta": paired["delta"],
                                    },
                                )
                            else:
                                payload = await self._on_promotion_recommended(
                                    child, paired["delta"], samples
                                )
                                await self._emit("universe.promotion_recommended", payload)

            await self._emit("universe.evolved", result.to_dict())
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("[EVOLVER] run failed")
            result.errors.append(str(e))
            return result
        finally:
            self._storage.release_synthesis_lock(_LOCK_KEY)

    async def _propose_mutation(self, champ: dict) -> dict:
        challengers = [
            u
            for u in self._mgr.list(include_archived=False)
            if u["status"] == UniverseStatus.CHALLENGER.value
        ]
        prompt = _USER.format(
            champion_fitness=json.dumps(champ.get("fitness", {}), ensure_ascii=False),
            challenger_fitness=json.dumps(
                [c.get("fitness", {}) for c in challengers], ensure_ascii=False
            ),
            summary=self._champion_summary(champ),
            profile=self._user_profile(),
            history=self._mutation_history(),
        )
        for _ in range(3):
            try:
                resp = await self._llm.chat(
                    messages=[
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": prompt},
                    ]
                )
                text = (getattr(resp, "content", None) or "").strip()
                if text.startswith("```") and "\n" in text:
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                prompt += "\n\n上次输出非合法 JSON，严格只输出 JSON。"
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)
        return {}

    def _user_profile(self, limit: int = 1500) -> str:
        """起居注蒸馏的主上画像(画像驱动进化,迭代 6):变异 prompt 注入,方向贴合偏好。
        provider 缺失或读取失败时降级为占位——失败安全,画像缺席不阻断变异。"""
        if self._profile_provider is None:
            return "(暂无画像)"
        try:
            text = (self._profile_provider() or "").strip()
        except Exception:  # noqa: BLE001
            return "(暂无画像)"
        if not text:
            return "(暂无画像)"
        return text[:limit] + ("…" if len(text) > limit else "")

    def _champion_summary(self, champ: dict) -> str:
        store = self._mgr._store  # noqa: SLF001
        pdir = store.personas_dir(champ["id"])
        personas = sorted(p.name for p in pdir.glob("*")) if pdir.exists() else []
        return f"人格: {personas}; config: {list(store.read_manifest(champ['id']).keys())}"

    async def _evaluate_behavior_challenger(self, child_id: str, cfg: Any) -> dict | None:
        """行为层候选的沙箱配对评估:主仓代码 + env 重定向 personas 到候选快照。

        协作者缺失(测试装配/未开代码变体基建)或无历史 goal 时返回 None,
        候选留观、不判晋升——失败安全,评估缺席不等于劣质。
        """
        if self._eval_harness is None or self._code_store is None:
            return None
        eval_set = self._eval_harness.select_eval_set(
            getattr(cfg, "code_variant_eval_set_size", 20)
        )
        if not eval_set:
            return None
        store = self._mgr._store  # noqa: SLF001
        variant_env = {"TIANSHU_RUNTIME_PERSONAS_DIR": str(store.personas_dir(child_id))}
        champion_key = self._baseline_key()
        fp = self._eval_harness.eval_set_fingerprint(eval_set, champion_key)
        cached = self._storage.latest_baseline_fitness(fp)
        budget = getattr(cfg, "code_variant_eval_budget_cny", None)
        repo_root = self._code_store.repo_root
        paired = await asyncio.to_thread(
            self._eval_harness.evaluate_paired,
            repo_root,
            eval_set=eval_set,
            baseline_worktree=repo_root,
            variant_env=variant_env,
            budget_cny=budget,
            cached_baseline=cached,
        )
        from datetime import UTC, datetime

        from ulid import ULID

        fitness = paired["variant"]["fitness"]
        if paired["variant"].get("truncated"):
            fitness = {**fitness, "truncated": True}
        self._storage.save_variant_eval_run(
            {
                "id": str(ULID()),
                "universe_id": child_id,
                "gate_passed": True,
                "fitness": fitness,
                "baseline": (
                    paired["baseline"]["fitness"]
                    if not paired["baseline"].get("truncated")
                    else None
                ),
                "eval_set_version": fp,
                "cost": paired["variant"]["stats"].get("cost", 0),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        self._storage.update_universe_fitness(child_id, fitness)
        return paired

    def _baseline_key(self) -> str:
        """基线身份 = 冠军位面 + 主干代码版本;主干任何提交都使基线缓存失效。"""
        champ = self._mgr.champion_id() or "genesis"
        head = "nohead"
        try:
            if self._code_store is not None:
                import subprocess

                proc = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(self._code_store.repo_root),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    head = proc.stdout.strip()
        except Exception:  # noqa: BLE001
            pass
        return f"{champ}:{head}"

    async def propose_code_variant(
        self,
        *,
        target_path: str,
        hypothesis: str,
        parent_id: str | None = None,
    ) -> dict:
        """代码变体提案：分支→变异→门禁→评估→记录→推荐（默认不自动晋升）。失败安全。

        返回 {"status","universe_id","fitness","detail"}（按需）。
        status ∈ {"disabled","no_collaborators","no_champion","no_mutation",
                  "gate_failed","evaluated","recommended","error"}。
        """
        try:
            cfg = self._config.agent_config
            if not getattr(cfg, "code_variant_enabled", False):
                return {"status": "disabled"}

            if any(
                c is None
                for c in (self._code_store, self._gate, self._eval_harness, self._code_mutator)
            ):
                return {"status": "no_collaborators"}

            parent = parent_id or self._mgr.champion_id()
            if not parent:
                return {"status": "no_champion"}

            from datetime import UTC, datetime

            from ulid import ULID

            child = self._mgr.branch_code_variant(
                parent,
                name=f"code:{target_path}",
                description=hypothesis,
            )
            uid = child["id"]
            worktree = self._code_store.worktree_dir(uid)

            m = await self._code_mutator.mutate(
                worktree,
                target_path=target_path,
                hypothesis=hypothesis,
            )
            if not m["applied"]:
                self._mgr.archive(uid)
                return {"status": "no_mutation", "universe_id": uid, "detail": m["reason"]}

            g = await asyncio.to_thread(self._gate.run, worktree)
            if not g.passed:
                self._storage.save_variant_eval_run(
                    {
                        "id": str(ULID()),
                        "universe_id": uid,
                        "gate_passed": False,
                        "gate_detail": {"stage": g.stage, "detail": g.detail[:1000]},
                        "fitness": {},
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                return {"status": "gate_failed", "universe_id": uid, "detail": g.stage}

            eval_set_size = getattr(cfg, "code_variant_eval_set_size", 20)
            es = self._eval_harness.select_eval_set(eval_set_size)
            champion_key = self._baseline_key()
            fp = self._eval_harness.eval_set_fingerprint(es, champion_key)
            cached = self._storage.latest_baseline_fitness(fp)
            budget = getattr(cfg, "code_variant_eval_budget_cny", None)
            paired = await asyncio.to_thread(
                self._eval_harness.evaluate_paired,
                worktree,
                eval_set=es,
                baseline_worktree=self._code_store.repo_root,
                budget_cny=budget,
                cached_baseline=cached,
            )
            fitness = paired["variant"]["fitness"]
            if paired["variant"].get("truncated"):
                fitness = {**fitness, "truncated": True}

            self._storage.save_variant_eval_run(
                {
                    "id": str(ULID()),
                    "universe_id": uid,
                    "gate_passed": True,
                    "fitness": fitness,
                    "baseline": (
                        paired["baseline"]["fitness"]
                        if not paired["baseline"].get("truncated")
                        else None
                    ),
                    "eval_set_version": fp,
                    "cost": paired["variant"]["stats"].get("cost", 0),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            self._storage.update_universe_fitness(uid, fitness)

            margin = getattr(cfg, "universe_promote_margin", 0.05)
            if paired["delta"] >= margin:
                return {
                    "status": "recommended",
                    "universe_id": uid,
                    "fitness": fitness,
                    "delta": paired["delta"],
                }
            return {
                "status": "evaluated",
                "universe_id": uid,
                "fitness": fitness,
                "delta": paired["delta"],
            }

        except Exception as e:  # noqa: BLE001
            logger.exception("[EVOLVER] propose_code_variant failed")
            return {"status": "error", "detail": str(e)}

    async def auto_propose_codes(self, trigger_source: str = "cron") -> dict:
        """自主代码提案:太医诊断 → 配额内逐个走 propose 闭环。失败安全。"""
        cfg = self._config.agent_config
        if not getattr(cfg, "code_variant_enabled", False) or not getattr(
            cfg, "code_variant_auto_propose", False
        ):
            return {"skipped": "disabled"}
        if self._diagnostician is None:
            return {"skipped": "no_diagnostician"}
        if trigger_source != "manual" and not self._idle_ok(
            getattr(cfg, "universe_evolver_idle_hours", 2)
        ):
            return {"skipped": "not_idle"}
        if not self._storage.try_acquire_synthesis_lock(_CODE_LOCK_KEY):
            return {"skipped": "lock_held"}
        try:
            quota = max(0, getattr(cfg, "code_variant_daily_propose_quota", 2))
            proposals = await self._diagnostician.diagnose(max_hypotheses=quota)
            results = []
            for p in proposals[:quota]:
                r = await self.propose_code_variant(
                    target_path=p["target_path"], hypothesis=p["hypothesis"]
                )
                results.append({"proposal": p, "result": r})
            out = {"proposed": len(results), "results": results}
            await self._emit("universe.code_proposed", out)
            return out
        except Exception as e:  # noqa: BLE001
            logger.exception("[EVOLVER] auto_propose_codes failed")
            return {"skipped": "error", "detail": str(e)}
        finally:
            self._storage.release_synthesis_lock(_CODE_LOCK_KEY)

    async def _emit(self, event_type: str, payload: dict) -> None:
        if not self._bus:
            return
        from tianshu.models.events import make_event

        self._bus.fire(
            make_event(
                event_type=event_type,
                edict_id=None,
                memorial_id=None,
                producer="universe_evolver",
                payload=payload,
            )
        )
