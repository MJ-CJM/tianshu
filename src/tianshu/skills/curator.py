"""SkillCurator (修撰) — periodic self-optimization of the agent skill library.

Mirrors ``ProfileSynthesizer``'s background-synthesis skeleton:
gate (idle + lock) → pure lifecycle transitions → collect candidates →
ONE structured-JSON LLM plan → deterministic apply (consolidate / archive) →
audit report + events.

Scope & safety:
- Operates GLOBALLY on agent-authored skills only (``created_by=='agent'``).
  Never touches builtin or manually-authored skills.
- Archives (recoverable via ``loader.restore_skill``), never deletes.
- Pinned skills are exempt. ``dry_run`` previews the plan without writing.

The display name is 「修撰」 — after the Hanlin Academy compiler (翰林院修撰),
the post Xie Jin held while compiling the Yongle Encyclopedia into 文渊阁.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tianshu.skills.curator_lifecycle import apply_automatic_transitions
from tianshu.skills.validator import SkillValidator

logger = logging.getLogger(__name__)

_LOCK_KEY = "__skill_curator__"

_SYSTEM = (
    "你是「修撰」——天书技能库的策展官，仿翰林修撰编纂校理之职。"
    "你只整理 agent 自建技能：合并近似、提炼上位「伞」技能、归档陈旧冗余。"
    "严格客观，禁止编造；只输出 JSON 对象，不带任何 markdown 代码块标记。"
)

_USER = """\
下面是当前 agent 自建技能（仅这些可被整理，不得新增库外内容）：

{candidates}

请产出一份「整理计划」，让技能库精炼、去重、聚焦：
- consolidations：把数个近似/同类技能合并成一个上位「伞」技能。
  into = 伞技能名（kebab-case，可复用其中一个被并入的名字或起新名）；
  into_content = 完整 SKILL.md（含 YAML frontmatter: name/description，自洽可独立使用）；
  absorb = 被并入并归档的技能名列表（建议 ≥2）。
- archivals：单纯陈旧/无价值、可直接归档（非删除）的技能名 + 原因。

规则：
- 归档而非删除；被 absorb 的技能会自动归档。
- 不要碰已归档的技能；没有可整理项时两个数组都留空。
- 不要臆造技能名，absorb/archivals 中的名字必须来自上面的清单。

只输出 JSON：
{{"consolidations": [{{"into": "...", "into_content": "---\\nname: ...\\ndescription: ...\\n---\\n...", "absorb": ["a", "b"], "reason": "..."}}],
  "archivals": [{{"name": "...", "reason": "..."}}]}}
"""


_ITERATE_SYSTEM = (
    "你是「修撰」——技能库质量校理。给定一个低效技能的 SKILL.md 与其指标，"
    "产出一份改进后的完整 SKILL.md（含 YAML frontmatter: name/description，name 不变）。"
    "聚焦：让指令更清晰、解释 why、去掉拖累执行的部分。只输出 SKILL.md 全文，不要代码块标记。"
)

_ITERATE_USER = """\
技能 `{name}` 成功率偏低（usage={usage} success_rate={rate}）。当前 SKILL.md：

{content}

请产出改进后的完整 SKILL.md（name 必须仍是 `{name}`）。"""


@dataclass
class CuratePlan:
    consolidations: list[dict] = field(default_factory=list)
    archivals: list[dict] = field(default_factory=list)


@dataclass
class CurateResult:
    skipped: str | None = None
    lifecycle: dict = field(default_factory=dict)
    candidates: int = 0
    plan: CuratePlan | None = None
    created: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    iterated: list[str] = field(default_factory=list)
    dry_run: bool = False
    report_dir: str | None = None

    def to_dict(self) -> dict:
        return {
            "skipped": self.skipped,
            "lifecycle": self.lifecycle,
            "candidates": self.candidates,
            "plan": {
                "consolidations": self.plan.consolidations,
                "archivals": self.plan.archivals,
            } if self.plan else None,
            "created": self.created,
            "archived": self.archived,
            "errors": self.errors,
            "iterated": self.iterated,
            "dry_run": self.dry_run,
            "report_dir": self.report_dir,
        }


def _age_hours(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


class SkillCurator:
    def __init__(
        self,
        llm_client: Any,
        loader: Any,
        metrics_store: Any,
        storage: Any,
        config_manager: Any,
        runtime_dir: Path,
        validator: SkillValidator | None = None,
    ) -> None:
        self._llm = llm_client
        self._loader = loader
        self._metrics = metrics_store
        self._storage = storage
        self._config = config_manager
        self._runtime_dir = Path(runtime_dir).expanduser()
        self._validator = validator or SkillValidator()
        self._event_bus: Any | None = None

    def attach_event_bus(self, bus: Any) -> None:
        self._event_bus = bus

    # --- gate ---

    def _idle_ok(self, idle_hours: int) -> bool:
        last = self._storage.last_activity_at()
        age = _age_hours(last)
        return age is None or age >= idle_hours

    # --- candidates ---

    def _collect_candidates(self) -> list[dict]:
        out: list[dict] = []
        for m in self._metrics.list_agent_created():
            if m.state == "archived" or m.pinned:
                continue
            skill = self._loader.get_skill(m.skill_name)
            if not skill:
                continue  # metrics row without a live file (already gone)
            out.append({
                "name": m.skill_name,
                "description": skill.get("description", ""),
                "usage": m.usage_count,
                "success": m.success_count,
                "state": m.state,
                "last_used": m.last_used_at or m.created_at or "?",
            })
        return out

    @staticmethod
    def _render_candidates(cands: list[dict]) -> str:
        return "\n".join(
            f"- {c['name']} | {c['description']} "
            f"| usage={c['usage']} success={c['success']} state={c['state']} last_used={c['last_used']}"
            for c in cands
        )

    # --- LLM ---

    async def _call_llm_json(self, system: str, user: str) -> dict:
        """3-attempt structured-JSON call (mirrors ProfileSynthesizer)."""
        last_err: Exception | None = None
        prompt_user = user
        for _ in range(3):
            try:
                resp = await self._llm.chat(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt_user},
                    ],
                )
                text = (getattr(resp, "content", None) or "").strip()
                if text.startswith("```") and "\n" in text:
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e
                prompt_user = user + "\n\n上次输出不是合法 JSON，严格只输出 JSON 对象。"
            except Exception as e:
                last_err = e
                await asyncio.sleep(1)
        logger.warning("[CURATOR] LLM json call failed after retries: %s", last_err)
        return {}

    async def _llm_plan(self, candidates: list[dict]) -> CuratePlan:
        raw = await self._call_llm_json(
            _SYSTEM, _USER.format(candidates=self._render_candidates(candidates)),
        )
        cons = raw.get("consolidations", []) if isinstance(raw, dict) else []
        arch = raw.get("archivals", []) if isinstance(raw, dict) else []
        return CuratePlan(
            consolidations=[c for c in cons if isinstance(c, dict)],
            archivals=[a for a in arch if isinstance(a, dict)],
        )

    # --- apply (deterministic) ---

    def _apply_plan(self, plan: CuratePlan) -> tuple[list[str], list[str], list[str]]:
        created: list[str] = []
        archived: list[str] = []
        errors: list[str] = []
        live = {m.skill_name: m for m in self._metrics.list_agent_created()}

        for c in plan.consolidations:
            into = (c.get("into") or "").strip()
            into_content = c.get("into_content") or ""
            absorb = [a for a in (c.get("absorb") or []) if a and a != into]
            if not into or not into_content or not absorb:
                errors.append(f"consolidation incomplete: into={into!r}")
                continue
            existing = self._loader.get_skill(into)
            # `into` may reuse an absorbed agent skill name, but must not shadow
            # a non-agent (builtin/manual) skill.
            if existing and into not in live:
                errors.append(f"into '{into}' collides with non-agent skill")
                continue
            v = self._validator.validate(into, into_content, source="agent-created")
            if not v.valid:
                msg = "; ".join(f.message for f in v.findings if f.level == "error")
                errors.append(f"into '{into}' invalid: {msg}")
                continue
            try:
                if existing:
                    self._loader.delete_skill(into)
                self._loader.create_skill(into, into_content)
                self._metrics.ensure_exists(into, created_by="agent")
                self._metrics.set_state(into, "active")
                created.append(into)
            except Exception as e:  # noqa: BLE001
                errors.append(f"consolidation '{into}' write failed: {e}")
                continue
            for name in absorb:
                if name not in live:
                    errors.append(f"absorb skip non-agent '{name}'")
                    continue
                if live[name].pinned:
                    errors.append(f"absorb skip pinned '{name}'")
                    continue
                if self._loader.archive_skill(name):
                    self._metrics.mark_archived(name, into=into)
                    archived.append(name)

        for a in plan.archivals:
            name = (a.get("name") or "").strip()
            if name not in live:
                errors.append(f"archival skip non-agent '{name}'")
                continue
            if live[name].pinned:
                errors.append(f"archival skip pinned '{name}'")
                continue
            if name in created:
                continue
            if self._loader.archive_skill(name):
                self._metrics.mark_archived(name)
                archived.append(name)

        return created, archived, errors

    async def _iterate_pass(self) -> list[str]:
        """Auto-improve low-success agent skills (not pinned/human-curated)."""
        cfg = self._config.agent_config
        improved: list[str] = []
        candidates = self._metrics.list_iteration_candidates(
            min_success_rate=getattr(cfg, "skill_iterate_min_success_rate", 0.5),
            min_usage=getattr(cfg, "skill_iterate_min_usage", 3),
        )
        for m in candidates:
            skill = self._loader.get_skill(m.skill_name)
            if not skill:
                continue
            try:
                resp = await self._llm.chat(messages=[
                    {"role": "system", "content": _ITERATE_SYSTEM},
                    {"role": "user", "content": _ITERATE_USER.format(
                        name=m.skill_name, usage=m.usage_count,
                        rate=m.success_rate, content=skill.get("content", ""))},
                ])
                new_md = (getattr(resp, "content", None) or "").strip()
                if new_md.startswith("```") and "\n" in new_md:
                    new_md = new_md.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                if not new_md:
                    continue
                v = self._validator.validate(m.skill_name, new_md, source="agent-created")
                if not v.valid:
                    continue
                self._loader.save_skill(m.skill_name, new_md)
                improved.append(m.skill_name)
            except Exception:  # noqa: BLE001
                logger.debug("[CURATOR] iterate failed for %s", m.skill_name, exc_info=True)
        return improved

    # --- audit ---

    async def _emit(self, event_type: str, payload: dict) -> None:
        if not self._event_bus:
            return
        from tianshu.models.events import make_event
        ev = make_event(
            event_type=event_type,
            edict_id=None,
            memorial_id=None,
            producer="skill_curator",
            payload=payload,
        )
        self._event_bus.fire(ev)

    def _write_report(self, result: CurateResult, trigger_source: str) -> str:
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        d = self._runtime_dir / "curator" / ts
        d.mkdir(parents=True, exist_ok=True)
        run = {"trigger_source": trigger_source, "ts": ts, **result.to_dict()}
        (d / "run.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        lines = [
            f"# 修撰策展报告 {ts}",
            "",
            f"- 触发: {trigger_source}",
            f"- 生命周期: {result.lifecycle}",
            f"- 候选技能数: {result.candidates}",
            f"- 新建/合并伞技能: {result.created or '(无)'}",
            f"- 归档: {result.archived or '(无)'}",
            f"- 错误: {result.errors or '(无)'}",
            f"- 单条迭代: {result.iterated or '(无)'}",
        ]
        (d / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(d)

    # --- orchestration ---

    async def run(
        self, trigger_source: str = "manual", dry_run: bool = False,
    ) -> CurateResult:
        cfg = self._config.agent_config
        if not getattr(cfg, "skill_curator_enabled", True):
            return CurateResult(skipped="disabled", dry_run=dry_run)

        if not dry_run:
            if not self._idle_ok(getattr(cfg, "skill_curator_idle_hours", 2)):
                await self._emit("curate.skipped",
                                 {"reason": "not_idle", "trigger_source": trigger_source})
                return CurateResult(skipped="not_idle")
            if not self._storage.try_acquire_synthesis_lock(_LOCK_KEY):
                await self._emit("curate.skipped",
                                 {"reason": "lock_held", "trigger_source": trigger_source})
                return CurateResult(skipped="lock_held")

        started = datetime.now(UTC)
        await self._emit("curate.started",
                         {"trigger_source": trigger_source, "dry_run": dry_run})
        try:
            lifecycle: dict = {}
            if not dry_run:
                lifecycle = apply_automatic_transitions(
                    self._metrics, self._loader,
                    stale_after_days=getattr(cfg, "skill_stale_after_days", 30),
                    archive_after_days=getattr(cfg, "skill_archive_after_days", 90),
                )

            candidates = self._collect_candidates()
            result = CurateResult(
                lifecycle=lifecycle, candidates=len(candidates), dry_run=dry_run,
            )

            if len(candidates) >= 2:
                plan = await self._llm_plan(candidates)
                result.plan = plan
                if not dry_run and (plan.consolidations or plan.archivals):
                    created, archived, errors = self._apply_plan(plan)
                    result.created, result.archived, result.errors = created, archived, errors

            if not dry_run:
                result.iterated = await self._iterate_pass()
                result.report_dir = self._write_report(result, trigger_source)

            await self._emit("curate.completed", {
                "trigger_source": trigger_source,
                "dry_run": dry_run,
                "lifecycle": lifecycle,
                "candidates": len(candidates),
                "created": result.created,
                "archived": result.archived,
                "errors_count": len(result.errors),
                "duration_ms": int((datetime.now(UTC) - started).total_seconds() * 1000),
            })
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("[CURATOR] run failed")
            await self._emit("curate.failed",
                             {"error_type": type(e).__name__, "error_message": str(e)})
            return CurateResult(skipped="error", errors=[str(e)], dry_run=dry_run)
        finally:
            if not dry_run:
                self._storage.release_synthesis_lock(_LOCK_KEY)
