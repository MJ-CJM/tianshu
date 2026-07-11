"""Auditor — two-layer audit: rules engine + optional LLM review."""

from __future__ import annotations

import logging

from tianshu.auditor.reviewer import LLMReviewer
from tianshu.auditor.rules import RulesEngine
from tianshu.auditor.rules_config import AuditRulesConfig
from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager
from tianshu.models.common import AuditResult, EdictStatus, TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class Auditor:
    """Subscribes to execution.completed, runs audit, emits audit.completed."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        config_manager: ConfigManager,
        rules_config: AuditRulesConfig | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        # 审计规则外部配置(YAML 可调)。默认 None → 内置默认,既有调用不破。
        # TODO(制度补全 D13): 目前仅作为 seam 存储。真正接入需把 self._rules_config
        #   下发给两处消费方:①RulesEngine(rules.py)按 check_* 开关门控三条规则、
        #   并用 risk_keywords 做命中扫描;②LLMReviewer(reviewer.py)用
        #   review_temperature / review_max_tokens 覆盖 LLMClient 调用参数。
        #   二者均在本次改动范围之外,故此处仅留接入点。参见 rules_config.AuditRulesConfig。
        self._rules_config = rules_config if rules_config is not None else AuditRulesConfig()
        self._rules = RulesEngine()
        self._reviewer = LLMReviewer(config_manager)

    async def audit(self, edict: Edict, memorial: Memorial) -> AuditResult:
        logger.debug("[AUDIT] Edict %s: start audit, policy=%s", edict.id, edict.review_policy)
        # Layer 1: fast rules
        result = self._rules.check(edict, memorial)

        # Layer 2: LLM review only if rules flagged
        if result.verdict == "flag" and edict.review_policy != "never":
            result = await self._reviewer.review(edict, memorial, result.reasons)

        logger.debug(
            "[AUDIT] Edict %s: verdict=%s, reasons=%s, llm_reviewed=%s",
            edict.id,
            result.verdict,
            result.reasons,
            result.verdict == "flag" and edict.review_policy != "never",
        )
        return result

    async def handle_execution_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for execution.completed."""
        edict_id = event.edict_id
        memorial_id = event.memorial_id
        if not edict_id or not memorial_id:
            return

        edict = self._storage.get_edict(edict_id)
        memorial = self._storage.get_memorial(memorial_id)
        if not edict or not memorial:
            logger.error(
                "Auditor: edict %s or memorial %s not found",
                edict_id,
                memorial_id,
            )
            return

        # Set AUDITING status
        if memorial.status == TaskStatus.COMPLETED:
            memorial.status = TaskStatus.AUDITING
            self._storage.update_memorial(memorial)

        # Skip audit if policy is "never"
        if edict.review_policy == "never":
            audit_result = AuditResult(verdict="pass", rules_checked=0)
        elif (
            edict.review_policy == "always"
            or edict.review_policy == "on_failure"
            and memorial.status == TaskStatus.FAILED
            or edict.review_policy == "on_flag"
        ):
            audit_result = await self.audit(edict, memorial)
        else:
            audit_result = AuditResult(verdict="pass", rules_checked=0)

        memorial.audit = audit_result

        # "always" policy: force human review regardless of audit verdict
        if edict.review_policy == "always":
            memorial.review_status = "pending"
            memorial.status = TaskStatus.NEEDS_REVIEW
        elif audit_result.verdict == "block":
            memorial.status = TaskStatus.FAILED
            memorial.error = "Blocked by audit: " + "; ".join(audit_result.reasons)
        elif audit_result.verdict == "flag":
            memorial.review_status = "pending"
            memorial.status = TaskStatus.NEEDS_REVIEW
        else:
            memorial.review_status = "not_required"
            memorial.status = TaskStatus.COMPLETED

        self._storage.update_memorial(memorial)

        # Auto-close edict if no human review required and execution succeeded.
        # 周期性敕令（cron/interval）每次运行后必须保持 OPEN，否则 _cron_loop /
        # _interval_loop 下一轮会因 edict 非 open 而停止、重启时 _restore_jobs 也会取消。
        if (
            memorial.status == TaskStatus.COMPLETED
            and memorial.review_status == "not_required"
            and edict.status == EdictStatus.OPEN
            and edict.schedule.type not in ("cron", "interval")
        ):
            self._storage.update_edict_status(edict.id, EdictStatus.COMPLETED.value)

        await self._bus.emit(
            make_event(
                "audit.completed",
                edict_id=edict_id,
                memorial_id=memorial_id,
                producer="auditor",
                payload={
                    "verdict": audit_result.verdict,
                    "reasons": audit_result.reasons,
                },
            )
        )
