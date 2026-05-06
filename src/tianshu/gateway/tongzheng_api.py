"""通政司 API：对外通信通道配置（飞书 / 钉钉 / 邮件）。

v1 仅支持飞书。配置加载优先级：DB > env > 不启用。
保存敏感凭证需要 TIANSHU_SECRET_MASTER_KEY 环境变量（Fernet 主密钥）。
保存后自动热加载，不重启服务。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tianshu.models.common import ApiResponse
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

tongzheng_router = APIRouter(prefix="/tongzheng", tags=["tongzheng"])


class FeishuChannelConfig(BaseModel):
    """飞书通道配置（写入用）。app_secret 单独提交。"""

    app_id: str = ""
    app_secret: str | None = Field(
        default=None,
        description="None=不修改；空串=清空；非空=替换",
    )
    domain: str = "feishu"
    connection_mode: str = "websocket"
    allowed_users: str = ""
    home_channel: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    bot_open_id: str = ""
    bot_name: str = ""
    webhook_path: str = "/feishu/webhook"
    ws_reconnect_interval: int = 120
    text_batch_delay: float = 0.6
    dedup_cache_size: int = 2048
    assistant_persona_id: str = "tongzheng"
    intent_llm_enabled: bool = True
    enable_edict_submission: bool = False


def _build_feishu_settings_from_runtime(runtime_cfg: dict):
    """把含明文 secret 的 runtime dict 转为 FeishuSettings。"""
    from tianshu.gateway.feishu.settings import FeishuSettings

    return FeishuSettings(
        app_id=runtime_cfg.get("app_id", ""),
        app_secret=runtime_cfg.get("app_secret", ""),
        domain=runtime_cfg.get("domain", "feishu"),
        connection_mode=runtime_cfg.get("connection_mode", "websocket"),
        allowed_users=tuple(
            u.strip()
            for u in (runtime_cfg.get("allowed_users") or "").split(",")
            if u.strip()
        ),
        home_channel=runtime_cfg.get("home_channel", ""),
        encrypt_key=runtime_cfg.get("encrypt_key", ""),
        verification_token=runtime_cfg.get("verification_token", ""),
        bot_open_id=runtime_cfg.get("bot_open_id", ""),
        bot_name=runtime_cfg.get("bot_name", ""),
        webhook_path=runtime_cfg.get("webhook_path", "/feishu/webhook"),
        ws_reconnect_interval=int(runtime_cfg.get("ws_reconnect_interval", 120)),
        text_batch_delay=float(runtime_cfg.get("text_batch_delay", 0.6)),
        dedup_cache_size=int(runtime_cfg.get("dedup_cache_size", 2048)),
        assistant_persona_id=runtime_cfg.get("assistant_persona_id", "tongzheng"),
        intent_llm_enabled=bool(runtime_cfg.get("intent_llm_enabled", True)),
        disable_assistant_mode=bool(runtime_cfg.get("disable_assistant_mode", False)),
        enable_edict_submission=bool(runtime_cfg.get("enable_edict_submission", False)),
    )


@tongzheng_router.get("/channels/feishu")
async def get_feishu_channel(request: Request) -> ApiResponse:
    """获取飞书通道配置（app_secret 永远以掩码返回，不返明文）。"""
    storage: Storage = request.app.state.storage
    cfg = storage.get_channel_config("feishu")
    if cfg is None:
        # 未在 DB 配置 → 返回从 env 推导的当前生效值（只读视图，方便用户从 env 迁移到 DB）
        from tianshu.config import TianshuSettings
        from tianshu.gateway.feishu.settings import from_global_settings

        s = from_global_settings(TianshuSettings())
        return ApiResponse(
            success=True,
            data={
                "app_id": s.app_id,
                "app_secret": "***" if s.app_secret else "",
                "domain": s.domain,
                "connection_mode": s.connection_mode,
                "allowed_users": ",".join(s.allowed_users),
                "home_channel": s.home_channel,
                "encrypt_key": s.encrypt_key,
                "verification_token": s.verification_token,
                "bot_open_id": s.bot_open_id,
                "bot_name": s.bot_name,
                "webhook_path": s.webhook_path,
                "ws_reconnect_interval": s.ws_reconnect_interval,
                "text_batch_delay": s.text_batch_delay,
                "dedup_cache_size": s.dedup_cache_size,
                "assistant_persona_id": s.assistant_persona_id,
                "intent_llm_enabled": s.intent_llm_enabled,
                "enable_edict_submission": s.enable_edict_submission,
                "_source": "env",
                "_has_secret": bool(s.app_secret),
            },
        )
    cfg["app_secret"] = "***" if cfg.get("_has_secret") else ""
    cfg["_source"] = "db"
    # 兼容老的 channel_configs 行（无新字段时回填默认）
    cfg.setdefault("assistant_persona_id", "tongzheng")
    cfg.setdefault("intent_llm_enabled", True)
    cfg.setdefault("enable_edict_submission", False)
    return ApiResponse(success=True, data=cfg)


@tongzheng_router.put("/channels/feishu")
async def put_feishu_channel(
    body: FeishuChannelConfig, request: Request,
) -> ApiResponse:
    """保存飞书配置 + 触发热加载。"""
    storage: Storage = request.app.state.storage
    config_dict = body.model_dump(exclude={"app_secret"})

    try:
        storage.save_channel_config(
            "feishu",
            config_dict,
            secret_plaintext=body.app_secret,  # None=不动；空串=清空；非空=更新
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 热加载
    bot = getattr(request.app.state, "feishu_bot", None)
    runtime_cfg = storage.load_channel_runtime_config("feishu")
    if runtime_cfg is None:
        return ApiResponse(
            success=True,
            data={"reloaded": False, "reason": "secret_decrypt_failed_or_unset"},
        )

    reload_ok = False
    reload_msg = ""
    try:
        new_settings = _build_feishu_settings_from_runtime(runtime_cfg)
        new_settings.validate_or_raise()
    except (RuntimeError, TypeError, ValueError) as e:
        return ApiResponse(
            success=True,
            data={"reloaded": False, "reason": f"config invalid: {e}"},
        )

    if bot is not None:
        try:
            await bot.reload(new_settings)
            reload_ok = True
        except Exception as e:
            logger.exception("[tongzheng] feishu bot reload failed")
            reload_msg = str(e)
    else:
        # 之前未启用机器人 → 现在启用：v1 不支持运行时新建 bot 实例（需要重启）
        reload_msg = "feishu bot 未运行，配置已保存，下次重启生效"

    # 同步敕令管理工具集启用状态（颁敕权限 toggle 同时管控读写工具）
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is not None:
        for tool_name in ("submit_edict", "list_edicts", "get_edict_status"):
            if new_settings.enable_edict_submission:
                tool_registry.enable(tool_name)
            else:
                tool_registry.disable(tool_name)

    return ApiResponse(
        success=True,
        data={"reloaded": reload_ok, "reason": reload_msg or "ok"},
    )


@tongzheng_router.get("/personas")
async def list_personas(request: Request) -> ApiResponse:
    """供前端下拉框使用：列所有可用 cabinet personas。"""
    loader = request.app.state.persona_loader
    items: list = []
    if hasattr(loader, "list"):
        items = list(loader.list())
    elif hasattr(loader, "personas"):
        items = list(loader.personas.values())
    else:
        items = list(getattr(loader, "_personas", {}).values())
    personas = []
    for p in items:
        personas.append({
            "id": getattr(p, "id", ""),
            "name": getattr(p, "name", ""),
            "department": getattr(p, "department", ""),
        })
    return ApiResponse(success=True, data={"personas": personas})


@tongzheng_router.get("/channels/feishu/status")
async def feishu_status(request: Request) -> ApiResponse:
    """查询当前飞书机器人连接状态。"""
    bot = getattr(request.app.state, "feishu_bot", None)
    if bot is None:
        return ApiResponse(success=True, data={"running": False, "mode": None})
    return ApiResponse(
        success=True,
        data={
            "running": True,
            "mode": bot._settings.connection_mode,
            "app_id": bot._settings.app_id,
        },
    )
