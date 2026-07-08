"""OTel GenAI 埋点薄封装(迭代 3「深防御」)——默认关,优雅降级。

尽调结论(roadmap P1-C):OTel GenAI semconv 仍 experimental(gen_ai.* 已迁
独立仓、无正式 release)。故在平台内做**一层薄封装集中定义 gen_ai.* 属性**,
升级只改这一处;不锁 OpenInference 私有命名空间。

激活条件(两个都满足才真埋点,否则全程 no-op、零成本):
1. ``TIANSHU_OTEL_ENDPOINT`` 已配(如 Phoenix 的 http://localhost:4318);
2. ``opentelemetry-sdk`` 已装(``pip install 'tianshu[otel]'``)。

任一不满足 → ``genai_span`` 返回空上下文管理器,调用点零改动、零依赖。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)

# --- gen_ai.* 属性名集中定义(semconv experimental,升级只改这里) ---
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
# 天枢自有维度(明制隐喻),非 semconv:加 tianshu. 前缀避免与标准冲突
TIANSHU_EDICT_ID = "tianshu.edict.id"
TIANSHU_PERSONA_ID = "tianshu.persona.id"

_tracer: Any | None = None
_enabled = False


def init_tracing(settings) -> bool:
    """按 settings 初始化 tracing;返回是否真正启用。lifespan 调一次。"""
    global _tracer, _enabled
    endpoint = getattr(settings, "otel_endpoint", "") or ""
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "[otel] TIANSHU_OTEL_ENDPOINT 已配但未装 opentelemetry;"
            "运行 `pip install 'tianshu[otel]'` 后生效(当前埋点跳过)"
        )
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": "tianshu"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("tianshu")
    _enabled = True
    logger.info("[otel] GenAI 埋点已启用 → %s", endpoint)
    return True


@contextlib.contextmanager
def genai_span(
    operation: str,
    *,
    model: str | None = None,
    edict_id: str | None = None,
    persona_id: str | None = None,
) -> Iterator[Any]:
    """一次 LLM 调用的 inference span;未启用时零成本 no-op。

    退出前调用方可 ``span.set_attribute`` 补 usage(见 record_usage)。
    """
    if not _enabled or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(f"gen_ai.{operation}") as span:
        span.set_attribute(GEN_AI_OPERATION_NAME, operation)
        span.set_attribute(GEN_AI_SYSTEM, "litellm")
        if model:
            span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        if edict_id:
            span.set_attribute(TIANSHU_EDICT_ID, edict_id)
        if persona_id:
            span.set_attribute(TIANSHU_PERSONA_ID, persona_id)
        yield span


def record_usage(span: Any, *, input_tokens: int, output_tokens: int) -> None:
    """把 usage 写到 span;span 为 None(未启用)时无操作。"""
    if span is None:
        return
    span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
    span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
