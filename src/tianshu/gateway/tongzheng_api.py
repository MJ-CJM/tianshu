"""通政司 API：对外通信通道配置（飞书 / 钉钉 / 邮件）。

v1 仅支持飞书。配置加载优先级：DB > env > 不启用。
保存敏感凭证需要 TIANSHU_SECRET_MASTER_KEY 环境变量（Fernet 主密钥）。
保存后自动热加载，不重启服务。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ulid import ULID

from tianshu.gateway.instance import default_instance_id
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


def _env_feishu_view() -> dict:
    """无 DB 默认实例时，从 env 推导的只读飞书配置视图。"""
    from tianshu.config import TianshuSettings
    from tianshu.gateway.feishu.settings import from_global_settings

    s = from_global_settings(TianshuSettings())
    return {
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
    }


@tongzheng_router.get("/channels/feishu")
async def get_feishu_channel(request: Request) -> ApiResponse:
    """获取飞书默认实例配置（app_secret 永远以掩码返回，不返明文）。"""
    storage: Storage = request.app.state.storage
    row = storage.get_channel_instance(default_instance_id("feishu"))
    if row is None:
        # 未在 DB 配置 → 返回从 env 推导的当前生效值（只读视图，方便用户从 env 迁移到 DB）
        return ApiResponse(success=True, data=_env_feishu_view())
    cfg = _mask_instance(row)
    cfg["app_secret"] = "***" if row.get("_has_secret") else ""
    cfg["_source"] = "db"
    # 兼容缺字段（无新字段时回填默认）
    cfg.setdefault("assistant_persona_id", "tongzheng")
    cfg.setdefault("intent_llm_enabled", True)
    cfg.setdefault("enable_edict_submission", False)
    return ApiResponse(success=True, data=cfg)


@tongzheng_router.put("/channels/feishu")
async def put_feishu_channel(
    body: FeishuChannelConfig, request: Request,
) -> ApiResponse:
    """保存飞书默认实例配置 + 触发热加载（薄封装到实例代码路径）。"""
    storage: Storage = request.app.state.storage
    instance_id = default_instance_id("feishu")
    config_dict = body.model_dump(exclude={"app_secret"})

    try:
        storage.save_channel_instance(
            instance_id=instance_id,
            channel_type="feishu",
            label="默认",
            enabled=True,
            config=config_dict,
            secret_plaintext=body.app_secret,  # None=不动；空串=清空；非空=更新
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = await _bring_instance_live(request, instance_id)
    _sync_edict_tools(request)
    return ApiResponse(success=True, data=result)


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
    """查询飞书默认实例连接状态。"""
    bot_manager = getattr(request.app.state, "bot_manager", None)
    bot = (
        bot_manager.get(default_instance_id("feishu"))
        if bot_manager is not None
        else None
    )
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


# ===================== Telegram（与飞书并列）=====================


class TelegramChannelConfig(BaseModel):
    """Telegram 通道配置（写入用）。bot_token 单独提交。"""

    bot_token: str | None = Field(
        default=None,
        description="None=不修改；空串=清空；非空=替换",
    )
    connection_mode: str = "polling"
    allowed_users: str = ""
    home_channel: str = ""
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str = ""
    poll_timeout: int = 30
    text_batch_delay: float = 0.6
    dedup_cache_size: int = 2048
    assistant_persona_id: str = "tongzheng"
    enable_edict_submission: bool = False


def _build_telegram_settings_from_runtime(runtime_cfg: dict):
    """把含明文 bot_token 的 runtime dict 转为 TelegramSettings。"""
    from tianshu.gateway.telegram.settings import (
        TelegramSettings,
        _parse_allowed_users,
    )

    return TelegramSettings(
        bot_token=runtime_cfg.get("bot_token", ""),
        connection_mode=runtime_cfg.get("connection_mode", "polling"),
        allowed_users=_parse_allowed_users(runtime_cfg.get("allowed_users") or ""),
        home_channel=runtime_cfg.get("home_channel", ""),
        webhook_path=runtime_cfg.get("webhook_path", "/telegram/webhook"),
        webhook_secret=runtime_cfg.get("webhook_secret", ""),
        poll_timeout=int(runtime_cfg.get("poll_timeout", 30)),
        text_batch_delay=float(runtime_cfg.get("text_batch_delay", 0.6)),
        dedup_cache_size=int(runtime_cfg.get("dedup_cache_size", 2048)),
        assistant_persona_id=runtime_cfg.get("assistant_persona_id", "tongzheng"),
        disable_assistant_mode=bool(runtime_cfg.get("disable_assistant_mode", False)),
        enable_edict_submission=bool(runtime_cfg.get("enable_edict_submission", False)),
    )


def _env_telegram_view() -> dict:
    """无 DB 默认实例时，从 env 推导的只读 Telegram 配置视图。"""
    from tianshu.config import TianshuSettings
    from tianshu.gateway.telegram.settings import from_global_settings

    s = from_global_settings(TianshuSettings())
    return {
        "bot_token": "***" if s.bot_token else "",
        "connection_mode": s.connection_mode,
        "allowed_users": ",".join(str(u) for u in s.allowed_users),
        "home_channel": s.home_channel,
        "webhook_path": s.webhook_path,
        "webhook_secret": s.webhook_secret,
        "poll_timeout": s.poll_timeout,
        "text_batch_delay": s.text_batch_delay,
        "dedup_cache_size": s.dedup_cache_size,
        "assistant_persona_id": s.assistant_persona_id,
        "enable_edict_submission": s.enable_edict_submission,
        "_source": "env",
        "_has_secret": bool(s.bot_token),
    }


@tongzheng_router.get("/channels/telegram")
async def get_telegram_channel(request: Request) -> ApiResponse:
    """获取 Telegram 默认实例配置（bot_token 永远以掩码返回）。"""
    storage: Storage = request.app.state.storage
    row = storage.get_channel_instance(default_instance_id("telegram"))
    if row is None:
        return ApiResponse(success=True, data=_env_telegram_view())
    cfg = _mask_instance(row)
    cfg["bot_token"] = "***" if row.get("_has_secret") else ""
    cfg["_source"] = "db"
    cfg.setdefault("assistant_persona_id", "tongzheng")
    cfg.setdefault("enable_edict_submission", False)
    return ApiResponse(success=True, data=cfg)


@tongzheng_router.put("/channels/telegram")
async def put_telegram_channel(
    body: TelegramChannelConfig, request: Request,
) -> ApiResponse:
    """保存 Telegram 默认实例配置 + 触发热加载（薄封装到实例代码路径）。"""
    storage: Storage = request.app.state.storage
    instance_id = default_instance_id("telegram")
    config_dict = body.model_dump(exclude={"bot_token"})

    try:
        storage.save_channel_instance(
            instance_id=instance_id,
            channel_type="telegram",
            label="默认",
            enabled=True,
            config=config_dict,
            secret_plaintext=body.bot_token,  # None=不动；空串=清空；非空=更新
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = await _bring_instance_live(request, instance_id)
    _sync_edict_tools(request)
    return ApiResponse(success=True, data=result)


@tongzheng_router.get("/channels/telegram/status")
async def telegram_status(request: Request) -> ApiResponse:
    """查询 Telegram 默认实例连接状态。"""
    bot_manager = getattr(request.app.state, "bot_manager", None)
    bot = (
        bot_manager.get(default_instance_id("telegram"))
        if bot_manager is not None
        else None
    )
    if bot is None:
        return ApiResponse(success=True, data={"running": False, "mode": None})
    return ApiResponse(
        success=True,
        data={"running": True, "mode": bot._settings.connection_mode},
    )


# ===================== Bot 实例 CRUD（多实例）=====================


def _settings_for_instance(storage: Storage, instance_id: str):
    """从 DB 读实例 runtime → 构造带正确 instance_id 的 Settings；不存在/不可读返回 None。"""
    import dataclasses

    runtime = storage.load_channel_instance_runtime(instance_id)
    if runtime is None:
        return None
    ct = runtime.get("channel_type")
    if ct == "feishu":
        s = _build_feishu_settings_from_runtime(runtime)
    elif ct == "telegram":
        s = _build_telegram_settings_from_runtime(runtime)
    else:
        return None
    return dataclasses.replace(s, instance_id=instance_id)


def _sync_edict_tools(request: Request) -> None:
    """任一实例开启 enable_edict_submission → 启用敕令工具组；全部关闭 → 禁用。"""
    storage: Storage = request.app.state.storage
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is None:
        return
    any_enabled = any(
        bool(inst.get("enable_edict_submission"))
        for inst in storage.list_channel_instances()
    )
    for tool_name in (
        "submit_edict",
        "schedule_edict",
        "list_edicts",
        "get_edict_status",
        "list_personas",
    ):
        (tool_registry.enable if any_enabled else tool_registry.disable)(tool_name)


def _mask_instance(row: dict) -> dict:
    """掩码实例 secret：去掉明文字段，仅暴露 _has_secret。"""
    masked = dict(row)
    masked.pop("app_secret", None)
    masked.pop("bot_token", None)
    return masked


async def _bring_instance_live(request: Request, instance_id: str) -> dict:
    """启用路径：构造 settings → validate → reload_instance（运行中=reload，未运行=新建启动）。

    返回 {"reloaded": bool, "reason": str}。bot_manager 缺失时降级（不抛）。
    """
    storage: Storage = request.app.state.storage
    bot_manager = getattr(request.app.state, "bot_manager", None)
    if bot_manager is None:
        return {"reloaded": False, "reason": "manager unavailable, restart to apply"}

    settings = _settings_for_instance(storage, instance_id)
    if settings is None:
        return {"reloaded": False, "reason": "secret_decrypt_failed_or_unset"}
    try:
        settings.validate_or_raise()
    except (RuntimeError, TypeError, ValueError) as e:
        return {"reloaded": False, "reason": f"config invalid: {e}"}

    try:
        ok = await bot_manager.reload_instance(instance_id, settings)
    except Exception as e:
        logger.exception("[tongzheng] instance %s reload failed", instance_id)
        return {"reloaded": False, "reason": str(e)}
    return {"reloaded": ok, "reason": "ok" if ok else "start failed; see server log"}


class InstanceCreate(BaseModel):
    """新建 bot 实例。"""

    channel_type: str                            # "feishu" | "telegram"
    label: str = ""
    enabled: bool = True
    config: dict = Field(default_factory=dict)   # 非敏感配置字段
    secret: str = ""                             # bot_token / app_secret（空=未配）


class InstanceUpdate(BaseModel):
    """更新 bot 实例（字段省略=不改）。"""

    label: str | None = None
    enabled: bool | None = None
    config: dict | None = None
    secret: str | None = None                    # None=不改; ""=清空; 非空=替换


@tongzheng_router.get("/instances")
async def list_instances(request: Request) -> ApiResponse:
    """列所有实例，合并运行状态；secret 掩码。"""
    storage: Storage = request.app.state.storage
    bot_manager = getattr(request.app.state, "bot_manager", None)
    running = (
        {s["instance_id"]: s for s in bot_manager.status()}
        if bot_manager is not None
        else {}
    )
    instances = []
    for row in storage.list_channel_instances():
        item = _mask_instance(row)
        run = running.get(row["instance_id"])
        item["running"] = run is not None
        item["mode"] = run.get("mode") if run else None
        instances.append(item)
    return ApiResponse(success=True, data={"instances": instances})


@tongzheng_router.get("/instances/{instance_id}")
async def get_instance(instance_id: str, request: Request) -> ApiResponse:
    """单条实例；secret 掩码。"""
    storage: Storage = request.app.state.storage
    row = storage.get_channel_instance(instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail="实例不存在")
    bot_manager = getattr(request.app.state, "bot_manager", None)
    run = bot_manager.get(instance_id) if bot_manager is not None else None
    item = _mask_instance(row)
    item["running"] = run is not None
    item["mode"] = run._settings.connection_mode if run is not None else None
    return ApiResponse(success=True, data=item)


@tongzheng_router.post("/instances")
async def create_instance(body: InstanceCreate, request: Request) -> ApiResponse:
    """新建实例 + （若 enabled）即时拉起。"""
    if body.channel_type not in ("feishu", "telegram"):
        raise HTTPException(status_code=400, detail="channel_type 必须是 feishu 或 telegram")
    storage: Storage = request.app.state.storage
    instance_id = f"{body.channel_type}-{ULID()}"
    try:
        storage.save_channel_instance(
            instance_id=instance_id,
            channel_type=body.channel_type,
            label=body.label,
            enabled=body.enabled,
            config=body.config,
            secret_plaintext=(body.secret or None),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = {"reloaded": False, "reason": "disabled"}
    if body.enabled:
        result = await _bring_instance_live(request, instance_id)
    _sync_edict_tools(request)
    return ApiResponse(
        success=True,
        data={"instance_id": instance_id, **result},
    )


@tongzheng_router.put("/instances/{instance_id}")
async def update_instance(
    instance_id: str, body: InstanceUpdate, request: Request,
) -> ApiResponse:
    """更新实例 + 同步运行态（启用→reload/start，禁用→stop）。"""
    storage: Storage = request.app.state.storage
    existing = storage.get_channel_instance(instance_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="实例不存在")

    enabled = body.enabled if body.enabled is not None else existing["enabled"]
    label = body.label if body.label is not None else existing.get("label", "")
    config = body.config if body.config is not None else _strip_meta(existing)
    try:
        storage.save_channel_instance(
            instance_id=instance_id,
            channel_type=existing["channel_type"],
            label=label,
            enabled=enabled,
            config=config,
            secret_plaintext=body.secret,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    bot_manager = getattr(request.app.state, "bot_manager", None)
    if enabled:
        result = await _bring_instance_live(request, instance_id)
    elif bot_manager is not None:
        await bot_manager.stop_instance(instance_id)
        result = {"reloaded": False, "reason": "stopped"}
    else:
        result = {"reloaded": False, "reason": "manager unavailable, restart to apply"}
    _sync_edict_tools(request)
    return ApiResponse(success=True, data=result)


class _EnabledBody(BaseModel):
    enabled: bool


@tongzheng_router.patch("/instances/{instance_id}/enabled")
async def patch_instance_enabled(
    instance_id: str, body: _EnabledBody, request: Request,
) -> ApiResponse:
    """启停实例。"""
    storage: Storage = request.app.state.storage
    if storage.get_channel_instance(instance_id) is None:
        raise HTTPException(status_code=404, detail="实例不存在")
    storage.set_channel_instance_enabled(instance_id, body.enabled)

    bot_manager = getattr(request.app.state, "bot_manager", None)
    if body.enabled:
        await _bring_instance_live(request, instance_id)
    elif bot_manager is not None:
        await bot_manager.stop_instance(instance_id)
    _sync_edict_tools(request)
    return ApiResponse(success=True, data={"enabled": body.enabled})


@tongzheng_router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str, request: Request) -> ApiResponse:
    """删除实例（默认实例不可删）。"""
    if instance_id.endswith("-default"):
        raise HTTPException(
            status_code=400,
            detail="默认实例不可删除（承接历史无标记敕令的回执）",
        )
    storage: Storage = request.app.state.storage
    bot_manager = getattr(request.app.state, "bot_manager", None)
    if bot_manager is not None:
        await bot_manager.stop_instance(instance_id)
    storage.delete_channel_instance(instance_id)
    _sync_edict_tools(request)
    return ApiResponse(success=True, data={"deleted": instance_id})


@tongzheng_router.get("/instances/{instance_id}/status")
async def instance_status(instance_id: str, request: Request) -> ApiResponse:
    """单实例运行状态。"""
    bot_manager = getattr(request.app.state, "bot_manager", None)
    bot = bot_manager.get(instance_id) if bot_manager is not None else None
    if bot is None:
        return ApiResponse(success=True, data={"running": False, "mode": None})
    return ApiResponse(
        success=True,
        data={"running": True, "mode": bot._settings.connection_mode},
    )


def _strip_meta(row: dict) -> dict:
    """从实例行剥离运行时/元数据 key，仅保留可回写的 config 字段。"""
    meta = {
        "instance_id",
        "channel_type",
        "label",
        "enabled",
        "_has_secret",
        "updated_at",
        "app_secret",
        "bot_token",
    }
    return {k: v for k, v in row.items() if k not in meta and not k.startswith("_")}
