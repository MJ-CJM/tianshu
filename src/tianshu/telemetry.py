"""opt-in 遥测 —— 默认关,启用也只上报版本 + 启动事件(ADR-0003)。

治理价值观展示:「连遥测都给你批红级控制」。默认 off;``TIANSHU_TELEMETRY=on``
才启用;仅上报匿名的版本号 + 启动时间戳(北极星=周活跃下旨实例数的代理)。
无 endpoint 时只记本地遥测日志(可审计),永不静默外发。一行 env 永久关。

**不上报**:任何 edict 内容、goal、用户标识、凭证、成本明细。载荷字段在
``build_payload`` 里穷举,代码即声明。
"""

from __future__ import annotations

import logging

from tianshu import __version__

logger = logging.getLogger(__name__)


def is_enabled(telemetry: str) -> bool:
    return telemetry.strip().lower() == "on"


def build_payload(event: str, *, instance_id: str) -> dict:
    """遥测载荷 —— 字段在此穷举,不含任何任务内容/用户数据/凭证。"""
    return {
        "event": event,  # 例:"startup"
        "version": __version__,
        "instance_id": instance_id,  # 匿名实例标识(不含用户信息)
    }


async def emit_startup(settings, *, instance_id: str) -> None:
    """启动事件:默认关;开启后有 endpoint 才外发,否则只记本地日志。"""
    if not is_enabled(getattr(settings, "telemetry", "off")):
        return
    payload = build_payload("startup", instance_id=instance_id)
    endpoint = getattr(settings, "telemetry_endpoint", "") or ""
    if not endpoint:
        logger.info("[telemetry] (local-only, opt-in) %s", payload)
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(endpoint, json=payload)
        logger.info("[telemetry] startup reported to %s", endpoint)
    except Exception:  # noqa: BLE001 - 遥测永不影响启动
        logger.debug("[telemetry] startup emit failed (ignored)", exc_info=True)
