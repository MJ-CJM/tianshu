"""审计规则外部配置 —— 让审计阈值/开关/关键词经 YAML 外部可调。

设计原则:**失败安全**。缺省文件不存在、YAML 损坏、出现未知键、类型不符或数值
越界,一律回退到与 `auditor` 现状一致的安全默认值,绝不抛异常、绝不改变既有行为。
不加载 YAML 时,`AuditRulesConfig()` 的默认值与 `rules.py` / `reviewer.py` 中的
硬编码取值逐一对应,保证向后兼容。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 数值阈值的合法闭区间(越界回退默认)。
_TEMPERATURE_RANGE = (0.0, 1.0)
_MAX_TOKENS_RANGE = (1, 8192)


@dataclass(frozen=True, slots=True)
class AuditRulesConfig:
    """审计规则可调项(不可变)。

    每个字段都带与 `auditor` 现状一致的安全默认值,因此不加载 YAML 时行为不变。

    字段与现有代码的对应关系:

    - ``check_token_budget`` / ``check_execution_error`` / ``check_empty_result``
      —— 对应 ``rules.RulesEngine`` 三条同步规则的启用开关(现状:三条全开)。
    - ``review_temperature`` / ``review_max_tokens``
      —— 对应 ``reviewer.LLMReviewer`` 调用 LLM 时的 ``temperature`` / ``max_tokens``
      (现状:0.1 / 512)。
    - ``risk_keywords`` —— 结果文本的「命中即 flag」扫描词表;默认空表即无副作用。
    """

    check_token_budget: bool = True
    check_execution_error: bool = True
    check_empty_result: bool = True
    review_temperature: float = 0.1
    review_max_tokens: int = 512
    risk_keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """导出为普通 dict,便于 API 返回 / 调试展示(tuple 转 list 以利 JSON 序列化)。"""
        return {
            "check_token_budget": self.check_token_budget,
            "check_execution_error": self.check_execution_error,
            "check_empty_result": self.check_empty_result,
            "review_temperature": self.review_temperature,
            "review_max_tokens": self.review_max_tokens,
            "risk_keywords": list(self.risk_keywords),
        }


# 白名单:仅这些键会被采纳,其余键忽略并告警。
_KNOWN_KEYS = frozenset(
    {
        "check_token_budget",
        "check_execution_error",
        "check_empty_result",
        "review_temperature",
        "review_max_tokens",
        "risk_keywords",
    }
)


def load_audit_rules(path: str | Path | None) -> AuditRulesConfig:
    """从 YAML 加载审计规则,失败安全地回退默认。

    Args:
        path: YAML 文件路径;为 ``None`` 或文件不存在时返回全默认配置。

    Returns:
        解析出的 :class:`AuditRulesConfig`;任何异常/坏配置都不会抛出,而是回退默认。
    """
    default = AuditRulesConfig()

    if path is None:
        logger.debug("审计规则:未提供配置路径,使用全默认")
        return default

    file_path = Path(path)
    if not file_path.is_file():
        logger.debug("审计规则:配置文件 %s 不存在,使用全默认", file_path)
        return default

    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("审计规则:读取/解析 %s 失败(%s),使用全默认", file_path, exc)
        return default

    if raw is None:
        logger.debug("审计规则:配置文件 %s 为空,使用全默认", file_path)
        return default

    if not isinstance(raw, dict):
        logger.warning(
            "审计规则:%s 顶层应为映射,实际为 %s,使用全默认",
            file_path,
            type(raw).__name__,
        )
        return default

    return _apply_overrides(raw, default)


def _apply_overrides(raw: dict[str, Any], default: AuditRulesConfig) -> AuditRulesConfig:
    """用白名单键覆盖默认值;未知键忽略并告警,坏值回退对应字段默认。"""
    for key in sorted(set(raw) - _KNOWN_KEYS):
        logger.warning("审计规则:忽略未知配置键 %r", key)

    overrides: dict[str, Any] = {}
    if "check_token_budget" in raw:
        overrides["check_token_budget"] = _coerce_bool(
            raw["check_token_budget"], default.check_token_budget, "check_token_budget"
        )
    if "check_execution_error" in raw:
        overrides["check_execution_error"] = _coerce_bool(
            raw["check_execution_error"], default.check_execution_error, "check_execution_error"
        )
    if "check_empty_result" in raw:
        overrides["check_empty_result"] = _coerce_bool(
            raw["check_empty_result"], default.check_empty_result, "check_empty_result"
        )
    if "review_temperature" in raw:
        overrides["review_temperature"] = _coerce_float_in_range(
            raw["review_temperature"],
            default.review_temperature,
            "review_temperature",
            *_TEMPERATURE_RANGE,
        )
    if "review_max_tokens" in raw:
        overrides["review_max_tokens"] = _coerce_int_in_range(
            raw["review_max_tokens"],
            default.review_max_tokens,
            "review_max_tokens",
            *_MAX_TOKENS_RANGE,
        )
    if "risk_keywords" in raw:
        overrides["risk_keywords"] = _coerce_str_tuple(
            raw["risk_keywords"], default.risk_keywords, "risk_keywords"
        )

    return replace(default, **overrides)


def _coerce_bool(value: Any, default: bool, key: str) -> bool:
    """仅接受真正的 bool;否则回退默认。"""
    if isinstance(value, bool):
        return value
    logger.warning("审计规则 %s 期望 bool,得到 %r,回退默认 %r", key, value, default)
    return default


def _coerce_int_in_range(value: Any, default: int, key: str, low: int, high: int) -> int:
    """接受闭区间 [low, high] 内的 int(拒绝 bool);越界或类型不符回退默认。"""
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning("审计规则 %s 期望 int,得到 %r,回退默认 %r", key, value, default)
        return default
    if not low <= value <= high:
        logger.warning("审计规则 %s=%s 越界 [%s, %s],回退默认 %r", key, value, low, high, default)
        return default
    return value


def _coerce_float_in_range(value: Any, default: float, key: str, low: float, high: float) -> float:
    """接受闭区间 [low, high] 内的数值(拒绝 bool);越界或类型不符回退默认。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        logger.warning("审计规则 %s 期望数值,得到 %r,回退默认 %r", key, value, default)
        return default
    coerced = float(value)
    if not low <= coerced <= high:
        logger.warning("审计规则 %s=%s 越界 [%s, %s],回退默认 %r", key, coerced, low, high, default)
        return default
    return coerced


def _coerce_str_tuple(value: Any, default: tuple[str, ...], key: str) -> tuple[str, ...]:
    """接受字符串列表并转 tuple;非列表回退默认,列表内非字符串项忽略并告警。"""
    if not isinstance(value, list):
        logger.warning("审计规则 %s 期望字符串列表,得到 %r,回退默认 %r", key, value, default)
        return default
    kept: list[str] = []
    for item in value:
        if isinstance(item, str):
            kept.append(item)
        else:
            logger.warning("审计规则 %s 含非字符串项 %r,已忽略", key, item)
    return tuple(kept)
