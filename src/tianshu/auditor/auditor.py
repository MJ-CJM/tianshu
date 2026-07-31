"""Auditor — two-layer audit: rules engine + optional LLM review."""

from __future__ import annotations

import logging

from tianshu.auditor.reviewer import LLMReviewer
from tianshu.auditor.rules import RulesEngine
from tianshu.auditor.rules_config import AuditRulesConfig
from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager
from tianshu.executor.keqing.session_executor import is_conversational_executor
from tianshu.models.common import AuditResult, EdictStatus, TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class Auditor:
    """Audits terminal execution events and emits ``audit.completed``."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        config_manager: ConfigManager,
        rules_config: AuditRulesConfig | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._rules_config = rules_config if rules_config is not None else AuditRulesConfig()
        self._rules = RulesEngine(self._rules_config)
        self._reviewer = LLMReviewer(config_manager, self._rules_config)

    @property
    def rules_config(self) -> AuditRulesConfig:
        return self._rules_config

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
            result.llm_reviewed,
        )
        return result

    async def handle_execution_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for execution.completed."""
        await self._handle_terminal_execution(event, execution_failed=False)

    async def handle_execution_failed(self, event: EventEnvelope) -> None:
        """EventBus handler for execution.failed."""
        await self._handle_terminal_execution(event, execution_failed=True)

    async def _handle_terminal_execution(
        self,
        event: EventEnvelope,
        *,
        execution_failed: bool,
    ) -> None:
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

        execution_failed = execution_failed or memorial.status == TaskStatus.FAILED

        # A successful result enters the visible auditing phase.  Preserve a
        # failed terminal state while auditing so a reviewer pass can never
        # accidentally turn an executor failure into a successful execution.
        if not execution_failed and memorial.status == TaskStatus.COMPLETED:
            memorial.status = TaskStatus.AUDITING
            self._storage.update_memorial(memorial)

        should_audit = (
            edict.review_policy in {"always", "on_flag"}
            or edict.review_policy == "on_failure"
            and execution_failed
        )
        if should_audit:
            audit_result = await self.audit(edict, memorial)
        else:
            audit_result = AuditResult(verdict="pass", rules_checked=0)

        audit_result.execution_failed = execution_failed
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
            memorial.status = TaskStatus.FAILED if execution_failed else TaskStatus.COMPLETED

        self._storage.update_memorial(memorial)

        # Auto-close edict if no human review required and execution succeeded.
        # 周期性敕令（cron/interval）每次运行后必须保持 OPEN，否则 _cron_loop /
        # _interval_loop 下一轮会因 edict 非 open 而停止、重启时 _restore_jobs 也会取消。
        # 对话式客卿（pi RPC 会话档，支持 follow_up 连续对话）同理保持 OPEN，
        # 否则一次产出即 auto-close，用户无法「继续批示」连续追问。
        # runtime.conversation（对话模式）：百官/native 执行的显式选择——成功后
        # 保持 OPEN 由人工结案，follow_up 回放多轮上下文，等同与百官连续对话。
        if (
            memorial.status == TaskStatus.COMPLETED
            and not execution_failed
            and memorial.review_status == "not_required"
            and edict.status == EdictStatus.OPEN
            and edict.schedule.type not in ("cron", "interval")
            and not is_conversational_executor(getattr(edict.runtime, "executor", None))
            and not getattr(edict.runtime, "conversation", False)
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
