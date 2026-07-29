"""客卿 LLM 出口网关 —— OpenShell 凭证隔离的强制点。

客卿(pi)进程 env 里只有 scoped token + 本网关 baseUrl;它把 LLM 请求打到本网关,
网关按 token 校验后**上游注入真实 provider key**转发。**raw key 永不进客卿进程**。

治理四关(每请求):
1. Bearer 校验:token 未知/过期/已吊销 → 401;
2. 模型白名单:请求 model 不在 token 允许集 → 403;
3. 预算硬熔断:token 已 over-budget → 402(pi 归类额度耗尽、不重试)——**从事后杀进程
   上移为网关确定性断供**;
4. 归因 + 记账:注 X-Tianshu-Edict-Id/Run-Id;转发后按响应 usage record_spend。

上游转发 `_forward_fn` 可注入(测试替身);默认实现用 httpx 反代到真实 provider,
真实 key 从 settings/vault 取(不在本文件硬编码)。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response

from tianshu.secrets.scoped_token import ScopedTokenRecord, get_scoped_token_store

logger = logging.getLogger(__name__)

llm_gateway_router = APIRouter(tags=["keqing-llm-gateway"])


@dataclass
class ForwardResult:
    status_code: int
    content: bytes
    media_type: str = "application/json"
    cost_usd: float = 0.0


# 上游转发实现:(record, request_body, provider_slug) -> ForwardResult。默认 httpx 反代;
# 测试注入替身以隔离治理逻辑。
ForwardFn = Callable[[ScopedTokenRecord, bytes, str], Awaitable[ForwardResult]]


async def _default_forward(record: ScopedTokenRecord, body: bytes, provider: str) -> ForwardResult:
    # 真实反代:从 settings/vault 取该 provider 的 raw key,httpx 转发到真实 baseUrl,
    # 从响应 usage 估算 cost_usd。此处留最小实现骨架,真实上游接入在部署接线时补全
    # (需 provider→(base_url, key, 计价表)映射;与 llm.py/api_request.py 复用凭证注入)。
    raise NotImplementedError(
        "default upstream forward not wired; inject _forward_fn or complete provider routing"
    )


_forward_fn: ForwardFn = _default_forward


def set_forward_fn(fn: ForwardFn) -> None:
    """接线/测试用:替换上游转发实现。"""
    global _forward_fn
    _forward_fn = fn


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        # 兼容 Anthropic x-api-key 头(pi 可能用它承载 scoped token)
        return request.headers.get("x-api-key")
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _extract_model(body: bytes) -> str:
    import json

    try:
        data = json.loads(body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return ""
    return str(data.get("model") or "")


async def _handle(request: Request, provider: str) -> Response:
    store = get_scoped_token_store()
    token = _extract_bearer(request)
    if not token:
        return Response(
            content=b'{"error":"missing bearer token"}',
            status_code=401,
            media_type="application/json",
        )
    record = store.verify(token)
    if record is None:
        # 未知/过期/已吊销:不泄漏细节
        return Response(
            content=b'{"error":"invalid or expired token"}',
            status_code=401,
            media_type="application/json",
        )
    if record.is_over_budget():
        # 预算耗尽 → 402(pi 归类额度耗尽、不重试),硬熔断在此确定性发生
        return Response(
            content=b'{"error":"budget exhausted for this run"}',
            status_code=402,
            media_type="application/json",
        )
    body = await request.body()
    model = _extract_model(body)
    if not record.allows_model(model):
        return Response(
            content=b'{"error":"model not allowed for this run"}',
            status_code=403,
            media_type="application/json",
        )

    result = await _forward_fn(record, body, provider)
    # 记账:把本次真实花费累加到 token(供后续请求的 402 判定)
    store.record_spend(token, result.cost_usd)
    resp = Response(
        content=result.content,
        status_code=result.status_code,
        media_type=result.media_type,
    )
    # 归因头:逐 run 记账/审计
    resp.headers["X-Tianshu-Edict-Id"] = record.edict_id
    resp.headers["X-Tianshu-Run-Id"] = record.run_id
    return resp


@llm_gateway_router.post("/keqing/llm/{provider}/v1/messages")
async def anthropic_style(request: Request, provider: str) -> Response:
    """Anthropic messages 格式出口(pi anthropic-messages api)。"""
    return await _handle(request, provider)


@llm_gateway_router.post("/keqing/llm/{provider}/v1/chat/completions")
async def openai_style(request: Request, provider: str) -> Response:
    """OpenAI chat completions 格式出口(pi openai-completions api)。"""
    return await _handle(request, provider)
