"""litellm.Router 构建——LLM 可靠性配置化(spec P1-A / 迭代 1)。

尽调结论(docs/strategy/ plan 文档 §4 P1-A):fallbacks / 按错误类型的 RetryPolicy /
cooldown / 上下文窗口降级 litellm.Router SDK 全有,不自研重试框架;天枢要做的只是
**显式配置化**。错误分类语料(zeroclaw reliable.rs):认证错/请求错/内容策略错重试
无意义,一律 0 次;限流与超时按退避重试(Router 自动遵守 Retry-After)。

Router 只用于主执行路径(ProviderManager 构造的客户端);显式构造的 LLMClient
(沙箱评估凭证隔离 TIANSHU_EVAL_LLM_*、doctor 连通测试等)保持直连,不共享 Router,
以免破坏凭证隔离语义。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from litellm import Router
from litellm.types.router import RetryPolicy

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tianshu.config_manager import LLMConfigState

# 按错误类型的重试策略:业务型错误重试无意义,直接失败进 fallback 链
_RETRY_POLICY = RetryPolicy(
    AuthenticationErrorRetries=0,  # 认证错:key 不对,重试只会再错一次
    BadRequestErrorRetries=0,  # 请求体错误:确定性失败
    ContentPolicyViolationErrorRetries=0,  # 内容策略:重试无意义
    RateLimitErrorRetries=3,  # 限流:退避重试(Router 遵守 Retry-After)
    TimeoutErrorRetries=2,
    InternalServerErrorRetries=2,  # 5xx:短暂故障值得重试
)


def configs_fingerprint(configs: list[LLMConfigState]) -> tuple:
    """启用配置集的指纹——变更(增删改/启停)即触发 Router 重建。"""
    return tuple(
        (c.name, c.model, c.api_key, c.api_base or "", bool(c.enabled))
        for c in sorted(configs, key=lambda c: c.name)
    )


def build_router(
    configs: list[LLMConfigState],
    active_name: str | None = None,
) -> Router | None:
    """从启用的 LLM 配置构建 Router;不足 1 个启用配置时返回 None(调用方回退直连)。

    - 每个启用配置 = 一个 deployment,`model_name` 即配置名(保持"按名选型"语义)。
    - fallback 链:任一配置失败 → 依次降级到其余启用配置(active 优先在前)。
    - 上下文窗口降级共用同一条链(超窗错误不重试、直接换配置,zeroclaw 语料)。
    """
    enabled = [c for c in configs if c.enabled and c.api_key]
    if not enabled:
        return None

    model_list: list[dict[str, Any]] = []
    for cfg in enabled:
        params: dict[str, Any] = {"model": cfg.model, "api_key": cfg.api_key}
        if cfg.api_base:
            params["api_base"] = cfg.api_base
        model_list.append({"model_name": cfg.name, "litellm_params": params})

    names = [c.name for c in enabled]
    # active 排到链首,使非 active 配置失败时优先降级回主配置
    if active_name in names:
        ordered = [active_name] + [n for n in names if n != active_name]
    else:
        ordered = names
    fallbacks = [{n: [m for m in ordered if m != n]} for n in names] if len(names) > 1 else []

    try:
        return Router(
            model_list=model_list,
            fallbacks=fallbacks,
            context_window_fallbacks=fallbacks,
            retry_policy=_RETRY_POLICY,
            allowed_fails=2,  # 同一 deployment 连续失败 2 次进入冷却
            cooldown_time=30,  # 冷却 30s,期间流量走其余 deployment
            num_retries=1,  # 未被 RetryPolicy 覆盖的错误类型默认 1 次
        )
    except Exception as e:  # noqa: BLE001 - Router 构造即时校验模型名;失败宽容降级
        # 直连 acompletion 是调用时才校验模型,为保持行为兼容(含自定义/别名模型名),
        # Router 构造失败只降级不阻断——调用方拿到 None 后走直连路径。
        logger.warning("[llm-router] build failed, falling back to direct calls: %s", e)
        return None
