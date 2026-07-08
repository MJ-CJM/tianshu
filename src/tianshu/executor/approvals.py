"""Approval (decree) management — T3 real-time approval via asyncio.Event."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from tianshu.bus.event_bus import EventBus
from tianshu.models.common import TaskStatus
from tianshu.models.decree import Decree
from tianshu.models.edict import Edict, title_from_goal
from tianshu.models.events import make_event
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT = 300.0  # 5 minutes


class ApprovalManager:
    """Manages approval workflow for memorials that need human review."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        session_rule_store: object | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._session_rule_store = session_rule_store
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, Decree] = {}
        # Spec Section 4: 记录 wait_for_approval 时的 tool_name，方便 _handle_approve 生成 session rule
        self._pending_tool: dict[str, str] = {}
        # 长任务 outer loop L3 审批（独立队列，与 tool-call 审批并存）
        self._outer_loop_pending: dict[str, asyncio.Event] = {}
        self._outer_loop_results: dict[str, object] = {}  # HumanDecision
        self._outer_loop_payload: dict[str, dict] = {}  # 等审批时附带的展示数据（for UI）

    async def on_before_tool_call(self, **context: object) -> object:
        """Deprecated pre-Step-2 entry point.

        入口判断已迁移到 PolicyHook（Spec Section 3 规则 4 ApprovalRequiredListRule）。
        保留方法签名以兼容已有 HookRegistry 注册，但直接返回 None 放行。

        实时审批的 wait_for_approval / submit_decree 仍然由本类提供，
        由 PolicyHook 在 require_approval 分支里直接调用。
        """
        return None

    async def wait_for_approval(
        self,
        memorial_id: str,
        tool_name: str,
    ) -> Decree | None:
        """Block until a decree is submitted for this memorial, or timeout."""
        evt = asyncio.Event()
        self._pending[memorial_id] = evt
        self._pending_tool[memorial_id] = tool_name

        logger.info(
            "Waiting for approval on memorial %s (tool: %s)",
            memorial_id,
            tool_name,
        )

        try:
            await asyncio.wait_for(evt.wait(), timeout=APPROVAL_TIMEOUT)
            return self._results.pop(memorial_id, None)
        except TimeoutError:
            logger.warning(
                "Approval timeout for memorial %s, auto-rejecting",
                memorial_id,
            )
            return None
        finally:
            self._pending.pop(memorial_id, None)
            self._pending_tool.pop(memorial_id, None)

    # --- 长任务 outer loop L3 审批接口（独立于 tool-call 审批）---

    async def wait_for_outer_loop_decision(
        self,
        edict_id: str,
        payload: dict | None = None,
        timeout_seconds: float = 86400.0,
    ) -> object | None:
        """阻塞直到某 edict 的 outer-loop L3 审批被提交，或超时。

        payload：当前最佳产出 / critic feedback / 迭代轮数等，存供 UI 列表查询。
        返回 HumanDecision pydantic 对象；超时返 None（caller 按 on_approval_timeout 处理）。
        """
        evt = asyncio.Event()
        self._outer_loop_pending[edict_id] = evt
        if payload is not None:
            self._outer_loop_payload[edict_id] = payload
        logger.info(
            "Waiting for outer-loop approval on edict %s (timeout=%ds)",
            edict_id,
            int(timeout_seconds),
        )
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout_seconds)
            return self._outer_loop_results.pop(edict_id, None)
        except TimeoutError:
            logger.warning("Outer-loop approval timeout for edict %s", edict_id)
            return None
        finally:
            self._outer_loop_pending.pop(edict_id, None)
            self._outer_loop_payload.pop(edict_id, None)

    def submit_outer_loop_decision(self, edict_id: str, decision: object) -> bool:
        """前端 POST 决策时调；返 True 表示真触发了等待中的 wait_for_outer_loop_decision。"""
        if edict_id not in self._outer_loop_pending:
            logger.warning(
                "submit_outer_loop_decision: no edict '%s' is awaiting decision",
                edict_id,
            )
            return False
        self._outer_loop_results[edict_id] = decision
        self._outer_loop_pending[edict_id].set()
        return True

    def list_pending_outer_loop(self) -> list[dict]:
        """列出所有等审批的 outer-loop edict 及附带 payload。前端御书房用。"""
        out: list[dict] = []
        for edict_id, payload in self._outer_loop_payload.items():
            out.append(
                {
                    "edict_id": edict_id,
                    **payload,
                }
            )
        return out

    def list_pending_tool_calls(self) -> list[dict]:
        """List in-memory pending tool approvals enriched with metadata.

        Used by 御书房 (RoyalStudyPage) to render mid-execution tool approval
        cards. Each entry is built from `_pending` + `_pending_tool` plus the most
        recent `tool.approval_required` event for the memorial.
        """
        out: list[dict] = []
        for memorial_id, tool_name in list(self._pending_tool.items()):
            memorial = self._storage.get_memorial(memorial_id)
            if not memorial:
                continue
            # Reverse scan events for the latest tool.approval_required
            latest_payload: dict = {}
            latest_created_at: str | None = None
            try:
                rows = self._storage.get_events(memorial.edict_id)
            except Exception:
                rows = []
            for row in reversed(rows or []):
                if row.get("memorial_id") != memorial_id:
                    continue
                if row.get("event_type") != "tool.approval_required":
                    continue
                payload = row.get("payload") or {}
                if payload.get("tool_name") == tool_name:
                    latest_payload = payload
                    latest_created_at = row.get("created_at")
                    break
            out.append(
                {
                    "memorial_id": memorial_id,
                    "edict_id": memorial.edict_id,
                    "tool_name": tool_name,
                    "rule_id": latest_payload.get("rule_id"),
                    "reason": latest_payload.get("reason"),
                    "tool_tier": latest_payload.get("tool_tier"),
                    "args_summary": latest_payload.get("args_summary") or {},
                    "created_at": latest_created_at,
                }
            )
        return out

    async def submit_tool_decision(
        self,
        memorial_id: str,
        action: Literal["approve", "reject", "guide"],
        *,
        comment: str | None = None,
        grant_scope: Literal["once", "edict", "always"] | None = None,
        grant_reason: str | None = None,
        actor: str = "human",
    ) -> Decree:
        """Approve/reject a mid-execution tool call WITHOUT mutating memorial status.

        Unlike `submit_decree`, this method targets pending tool approvals raised
        by `PolicyHook._request_approval`. The memorial is still running — we must
        only unblock `wait_for_approval`, emit a decree event, and optionally
        persist a session rule via `grant_scope`.
        """
        if memorial_id not in self._pending:
            raise ValueError(
                f"No pending tool approval for memorial '{memorial_id}'",
            )

        memorial = self._storage.get_memorial(memorial_id)
        if not memorial:
            raise ValueError(f"Memorial '{memorial_id}' not found")

        # 安全降级：bash 类工具禁止 always scope（policy_store.assert_can_grant 硬约束）。
        # 前置检测，把 grant_scope 改为 once，并通过事件 payload 透出 downgraded 标记，
        # 让前端能给出"已降级为本次"的提示，避免用户误以为永久放行了。
        tool_name = self._pending_tool.get(memorial_id) or ""
        original_grant_scope = grant_scope
        downgrade_reason: str | None = None
        if action == "approve" and grant_scope == "always":
            try:
                from tianshu.tools.policy_store import assert_can_grant

                assert_can_grant(tool_name, "always")
            except ValueError as e:
                downgrade_reason = str(e)
                grant_scope = "once"
                logger.info(
                    "submit_tool_decision: downgrading grant_scope always→once for %r — %s",
                    tool_name,
                    e,
                )

        decree = Decree(
            memorial_id=memorial_id,
            action=action,
            comment=comment,
            actor=actor,
            grant_scope=grant_scope,
            grant_reason=grant_reason,
        )
        self._storage.save_decree(decree)

        event_type = (
            "decree.approved"
            if action == "approve"
            else "decree.guided"
            if action == "guide"
            else "decree.rejected"
        )
        await self._bus.emit(
            make_event(
                event_type,
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={
                    "decree_id": decree.id,
                    "comment": decree.comment,
                    "mid_execution": True,
                    "tool_name": tool_name,
                    "grant_scope": grant_scope,
                    "requested_grant_scope": original_grant_scope,
                    "grant_downgraded": downgrade_reason is not None,
                    "grant_downgrade_reason": downgrade_reason,
                    "actor": actor,
                },
            )
        )

        # session rule escalation — only on approve + edict/always scope
        if (
            action == "approve"
            and grant_scope
            and grant_scope != "once"
            and self._session_rule_store is not None
        ):
            try:
                await self._write_session_rule_from_decree(memorial, decree)
            except Exception:
                logger.exception(
                    "submit_tool_decision: failed to write session rule for decree %s",
                    decree.id,
                )

        # wake up the waiting tool call
        self._results[memorial_id] = decree
        evt = self._pending.get(memorial_id)
        if evt:
            evt.set()

        return decree

    async def submit_decree(self, decree: Decree) -> None:
        """Process a decree and update memorial status accordingly."""
        memorial = self._storage.get_memorial(decree.memorial_id)
        if not memorial:
            raise ValueError(f"Memorial '{decree.memorial_id}' not found")

        self._storage.save_decree(decree)

        if decree.action == "approve":
            await self._handle_approve(memorial, decree)
        elif decree.action == "reject":
            await self._handle_reject(memorial, decree)
        elif decree.action == "retry":
            await self._handle_retry(memorial, decree)
        elif decree.action == "amend":
            await self._handle_amend(memorial, decree)
        elif decree.action == "cancel":
            await self._handle_cancel(memorial, decree)

        # Wake up any waiting approval
        evt = self._pending.get(decree.memorial_id)
        if evt:
            self._results[decree.memorial_id] = decree
            evt.set()

    async def _handle_approve(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "approved"
        memorial.status = TaskStatus.COMPLETED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.approved",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id, "comment": decree.comment},
            )
        )

        # Spec Section 4: grant_scope 升级为 session rule
        if decree.grant_scope and decree.grant_scope != "once" and self._session_rule_store:
            await self._write_session_rule_from_decree(memorial, decree)

    async def _handle_reject(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.error = decree.comment or "Rejected by reviewer"
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.rejected",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id, "comment": decree.comment},
            )
        )

    async def _handle_retry(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        new_memorial = Memorial(
            edict_id=memorial.edict_id,
            instruction=memorial.instruction,
            attempt=memorial.attempt + 1,
            parent_memorial_id=memorial.id,
        )
        self._storage.save_memorial(new_memorial)

        await self._bus.emit(
            make_event(
                "decree.retry",
                edict_id=memorial.edict_id,
                memorial_id=new_memorial.id,
                producer="approval_manager",
                payload={
                    "decree_id": decree.id,
                    "original_memorial_id": memorial.id,
                    "attempt": new_memorial.attempt,
                },
            )
        )

    async def _handle_amend(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        if decree.amended_goal:
            new_edict = Edict(
                goal=decree.amended_goal,
                title=title_from_goal(decree.amended_goal),
                context=f"Amended from memorial {memorial.id}",
            )
            self._storage.save_edict(new_edict)
            await self._bus.emit(
                make_event(
                    "edict.submitted",
                    edict_id=new_edict.id,
                    producer="approval_manager",
                    payload={"goal": new_edict.goal, "amended_from": memorial.id},
                )
            )

    async def _handle_cancel(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.CANCELLED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.cancelled",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id},
            )
        )

    async def _write_session_rule_from_decree(
        self,
        memorial: Memorial,
        decree: Decree,
    ) -> None:
        """根据 decree.grant_scope 写 session rule，供后续调用直接命中。"""
        from tianshu.tools.policy_store import (
            assert_can_grant,
            compute_fingerprint,
            make_session_rule,
        )

        tool_name = self._pending_tool.get(decree.memorial_id) or ""
        if not tool_name:
            logger.warning(
                "decree %s: no tool_name recorded for memorial %s, skip session rule",
                decree.id,
                decree.memorial_id,
            )
            return

        # bash + always 被硬约束禁止
        try:
            assert_can_grant(tool_name, decree.grant_scope or "once")
        except ValueError as e:
            logger.warning("decree %s: %s — downgrading to once", decree.id, e)
            return

        args = self._fetch_latest_approval_args(memorial.id, memorial.edict_id, tool_name)
        fingerprint = compute_fingerprint(tool_name, args)

        rule = make_session_rule(
            tool_name=tool_name,
            arg_fingerprint=fingerprint,
            scope=decree.grant_scope,  # "edict" | "always"
            source="approval",
            reason=decree.grant_reason or f"granted by decree {decree.id}",
            edict_id=memorial.edict_id if decree.grant_scope == "edict" else None,
            granted_by_decree_id=decree.id,
        )
        try:
            await self._session_rule_store.create(rule)  # type: ignore[union-attr]
        except Exception:
            logger.exception("failed to create session rule from decree %s", decree.id)
            return

        self._storage.append_event(
            memorial.edict_id,
            memorial.id,
            "policy.session_rule_created",
            {
                "rule_id": rule.rule_id,
                "tool_name": tool_name,
                "scope": rule.scope,
                "source": rule.source,
                "arg_fingerprint": rule.arg_fingerprint,
                "decree_id": decree.id,
            },
        )

    def _fetch_latest_approval_args(
        self,
        memorial_id: str,
        edict_id: str,
        tool_name: str,
    ) -> dict:
        """从 events 表反查最近一次 tool.approval_required 的 args_summary。"""
        try:
            rows = self._storage.get_events(edict_id)
        except Exception:
            return {}
        for row in reversed(rows or []):
            if row.get("memorial_id") != memorial_id:
                continue
            if row.get("event_type") != "tool.approval_required":
                continue
            payload = row.get("payload") or {}
            if payload.get("tool_name") != tool_name:
                continue
            return payload.get("args_summary") or {}
        return {}
