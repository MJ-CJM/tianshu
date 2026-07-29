"""模型引用串正式文法：``provider/model[:thinking]``。

- provider 段为注册表里的 provider id（首个 ``/`` 之前）；缺省表示未限定。
- ``:thinking`` 后缀仅当取值在 THINKING_LEVELS 枚举内才剥离——保住
  ``ollama/qwen3:32b`` 这类模型 id 自带冒号的场景。
- litellm 模型串映射由 ProviderProfile.litellm_prefix 决定，替代按
  api_base 子串猜前缀的旧逻辑（llm.py::_resolve_model）。
"""

from __future__ import annotations

from dataclasses import dataclass

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelRef:
    provider_id: str  # "" = 未限定 provider
    model_id: str
    thinking: str = ""  # "" = 未指定


def parse_model_ref(raw: str) -> ModelRef:
    """解析 ``provider/model[:thinking]``；空串返回全空 ModelRef。"""
    text = (raw or "").strip()
    if not text:
        return ModelRef(provider_id="", model_id="")
    provider_id = ""
    rest = text
    if "/" in text:
        provider_id, rest = text.split("/", 1)
        provider_id = provider_id.strip()
    thinking = ""
    if ":" in rest:
        candidate_model, candidate_thinking = rest.rsplit(":", 1)
        if candidate_thinking in THINKING_LEVELS:
            rest = candidate_model
            thinking = candidate_thinking
    return ModelRef(provider_id=provider_id, model_id=rest.strip(), thinking=thinking)


def format_model_ref(ref: ModelRef) -> str:
    text = f"{ref.provider_id}/{ref.model_id}" if ref.provider_id else ref.model_id
    if ref.thinking:
        text = f"{text}:{ref.thinking}"
    return text


def normalize_for_allowlist(raw: str) -> str:
    """归一化到 ``provider/model``（剥 thinking 后缀），供白名单精确比对。"""
    ref = parse_model_ref(raw)
    return f"{ref.provider_id}/{ref.model_id}" if ref.provider_id else ref.model_id
