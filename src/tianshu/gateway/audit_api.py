"""审计与策略路由（audit + policy）：审计统计/规则/网络事件、会话级工具授权规则增删、策略统计与内置模板。无统一 prefix，路径写全。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.auditor.rules_config import AuditRulesConfig
from tianshu.gateway.auth import get_auth_context, hash_system_audit_identity
from tianshu.models import ApiResponse
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage import Storage

audit_router = APIRouter(tags=["audit"])


# --- Audit endpoints ---


@audit_router.get("/audit/stats")
def get_audit_stats(request: Request):
    storage: Storage = request.app.state.storage
    stats = storage.get_audit_stats()
    return ApiResponse(success=True, data=stats)


@audit_router.get("/audit/network-events")
def get_network_events(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    tool: str | None = Query(None),
    host: str | None = Query(None),
    status: str | None = Query(None, pattern="^(ok|error)$"),
):
    """返回带 details.network 的工具事件，支持 tool/host/status 过滤。

    Spec: 鸿胪寺独立化 plan §D.
    """
    storage: Storage = request.app.state.storage
    return storage.list_network_events(
        limit=limit,
        tool=tool,
        host=host,
        status=status,
    )


# --- Audit rules management (刑部·律典) ---


@audit_router.get("/audit/rules")
def get_audit_rules(request: Request):
    """Get configured audit rules and review policies."""
    auditor = getattr(request.app.state, "auditor", None)
    config = getattr(auditor, "rules_config", AuditRulesConfig())
    rules = [
        {
            "id": "token_budget",
            "name": "Token 预算检查",
            "description": "检查 Token 用量是否超过敕令预算限制",
            "enabled": config.check_token_budget,
            "severity": "flag",
        },
        {
            "id": "execution_error",
            "name": "执行错误检查",
            "description": "检查执行过程中是否有错误发生",
            "enabled": config.check_execution_error,
            "severity": "flag",
        },
        {
            "id": "empty_result",
            "name": "空结果检查",
            "description": "检查执行结果是否为空（无结果且无错误）",
            "enabled": config.check_empty_result,
            "severity": "flag",
        },
    ]
    if config.risk_keywords:
        rules.append(
            {
                "id": "risk_keywords",
                "name": "风险关键词检查",
                "description": f"检查结果是否命中 {len(config.risk_keywords)} 个已配置风险关键词",
                "enabled": True,
                "severity": "flag",
            }
        )
    review_policies = [
        {"value": "never", "label": "从不审计", "description": "跳过所有审计流程"},
        {"value": "on_failure", "label": "失败时审计", "description": "仅在执行失败时触发审计"},
        {"value": "on_flag", "label": "标记时审计", "description": "规则标记后触发 LLM 深度审阅"},
        {"value": "always", "label": "始终审计", "description": "无论结果如何都强制人工复核"},
    ]
    return ApiResponse(
        success=True,
        data={
            "rules": rules,
            "review_policies": review_policies,
        },
    )


# --- Policy endpoints (Spec Section 6) ---


@audit_router.get("/policy/session_rules")
async def list_session_rules(request: Request, scope: str = "all"):
    """List session rules. scope = 'edict' | 'always' | 'all'."""
    store = getattr(request.app.state, "session_rule_store", None)
    if store is None:
        return ApiResponse(success=True, data={"rules": []})
    if scope == "all":
        edict_rules = await store.list_by_scope(scope="edict")
        always_rules = await store.list_by_scope(scope="always")
        rules = edict_rules + always_rules
    else:
        rules = await store.list_by_scope(scope=scope)

    def _serialize(r):  # noqa: ANN001, ANN202
        return {
            "rule_id": r.rule_id,
            "tool_name": r.tool_name,
            "arg_fingerprint": r.arg_fingerprint,
            "scope": r.scope,
            "edict_id": r.edict_id,
            "granted_at": r.granted_at.isoformat(),
            "granted_by_decree_id": r.granted_by_decree_id,
            "source": r.source,
            "reason": r.reason,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }

    return ApiResponse(success=True, data={"rules": [_serialize(r) for r in rules]})


def _session_rule_audit_request(
    request: Request,
    *,
    rule_id: str,
    action: Literal[
        "policy.session_rule_created",
        "policy.session_rule_revoked",
    ],
) -> AppendSystemAuditRequest:
    context = get_auth_context(request)
    return AppendSystemAuditRequest(
        correlation_id=context.correlation_id,
        actor_digest=hash_system_audit_identity(context.principal.id),
        action=action,
        outcome="succeeded",
        reason_code="policy_allowed",
        subject_kind="session_rule",
        subject_digest=hash_system_audit_identity(rule_id),
        metadata={},
    )


@audit_router.post("/policy/session_rules", response_model=ApiResponse, status_code=201)
async def create_session_rule(request: Request):
    """Manually create a session rule (source='manual')."""
    from datetime import timedelta

    from tianshu.tools.policy_store import assert_can_grant, make_session_rule

    store = getattr(request.app.state, "session_rule_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="SessionRuleStore not configured")

    body = await request.json()
    tool_name: str = body.get("tool_name", "").strip()
    raw_scope = body.get("scope", "always")
    reason: str = body.get("reason", "").strip() or "手动添加"
    expires_days: int | None = body.get("expires_days")
    edict_id: str | None = body.get("edict_id")

    if not tool_name:
        raise HTTPException(status_code=422, detail="tool_name is required")
    if raw_scope not in ("edict", "always"):
        raise HTTPException(status_code=422, detail="scope must be 'edict' or 'always'")
    scope: Literal["edict", "always"] = raw_scope
    if scope == "edict" and not edict_id:
        raise HTTPException(status_code=422, detail="edict_id is required for edict scope")

    try:
        assert_can_grant(tool_name, scope)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    expires_after = timedelta(days=expires_days) if expires_days and expires_days > 0 else None

    rule = make_session_rule(
        tool_name=tool_name,
        arg_fingerprint="*",  # manual rules match any args
        scope=scope,
        source="manual",
        reason=reason,
        edict_id=edict_id,
        expires_after=expires_after,
    )
    await store.create(rule)

    storage: Storage = request.app.state.storage
    storage.append_system_audit(
        _session_rule_audit_request(
            request,
            rule_id=rule.rule_id,
            action="policy.session_rule_created",
        )
    )

    return ApiResponse(
        success=True,
        data={
            "rule_id": rule.rule_id,
            "tool_name": rule.tool_name,
            "scope": rule.scope,
            "source": rule.source,
        },
    )


@audit_router.delete("/policy/session_rules/{rule_id}", response_model=ApiResponse)
async def revoke_session_rule(rule_id: str, request: Request):
    """Manually revoke a session rule."""
    store = getattr(request.app.state, "session_rule_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="SessionRuleStore not configured")
    await store.revoke(rule_id)
    storage: Storage = request.app.state.storage
    storage.append_system_audit(
        _session_rule_audit_request(
            request,
            rule_id=rule_id,
            action="policy.session_rule_revoked",
        )
    )
    return ApiResponse(success=True, data={"rule_id": rule_id, "revoked": True})


@audit_router.get("/policy/stats")
def policy_stats(request: Request):
    """Aggregate today's allow/deny/require_approval/approved/rejected counts."""
    import json as _json

    storage: Storage = request.app.state.storage
    conn = storage._conn
    stats = {"allow": 0, "deny": 0, "require_approval": 0, "approved": 0, "rejected": 0}
    rows = conn.execute(
        """
        SELECT event_type, payload_json FROM events
        WHERE date(created_at) = date('now')
          AND event_type IN ('policy.decision', 'decree.approved', 'decree.rejected')
        """
    ).fetchall()
    for row in rows:
        typ = row[0]
        payload = row[1]
        if typ == "decree.approved":
            stats["approved"] += 1
        elif typ == "decree.rejected":
            stats["rejected"] += 1
        elif typ == "policy.decision":
            try:
                parsed = _json.loads(payload) if isinstance(payload, str) else (payload or {})
                verdict = parsed.get("verdict", "")
                if verdict in stats:
                    stats[verdict] += 1
            except Exception:
                pass
    return ApiResponse(success=True, data=stats)


@audit_router.get("/policy/templates")
def list_policy_templates():
    """List built-in PolicyProfile templates."""
    from tianshu.tools.policy_profile import BUILTIN_TEMPLATES

    data = [
        {
            "name": name,
            "allowed_paths": list(p.allowed_paths),
            "allowed_bash_prefixes": list(p.allowed_bash_prefixes),
            "tier_overrides": dict(p.tier_overrides),
            "auto_approve_max_tier": p.auto_approve_max_tier,
        }
        for name, p in BUILTIN_TEMPLATES.items()
    ]
    return ApiResponse(success=True, data={"templates": data})
