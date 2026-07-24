"""客卿与影子快照路由(迭代 3.5「客卿」)。

- 客卿:列出可用外部执行器(供边建敕选择);
- 影子快照:列出某 edict 的快照 + 一键 revert(放手四保险③)。

revert 是危险动作(覆盖工作区文件),但影子仓独立于用户 .git,且回滚本身
留一个新快照节点(可再向前),故属可逆操作;不设批红门,但留事件账本。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from tianshu.executor.keqing import list_adapters
from tianshu.models import ApiResponse

logger = logging.getLogger(__name__)

keqing_router = APIRouter(tags=["keqing"])

# 客卿 backend → CLI 二进制名(只读体检用)。
_BACKEND_BINARY = {
    "pi": "pi",
    "claude-code": "claude",
    "codex": "codex",
    "opencode": "opencode",
}


@keqing_router.get("/keqing/agents")
def get_keqing_agents():
    """可用客卿 backend 列表(前端执行器下拉:native + keqing:<agent>)。"""
    agents = ["native"] + [f"keqing:{name}" for name in list_adapters()]
    return ApiResponse(success=True, data=agents)


def _detect_installed_version(binary: str) -> str | None:
    """从 CLI 的 package.json **读文件**取安装版本(不 spawn 进程,遵守进程启动守卫)。

    npm 全局装的 CLI:bin 符号链接 → realpath 落在包目录内,向上找 package.json 读 version。
    非 npm 布局/找不到 → None。best-effort,任何异常吞掉。
    """
    path = shutil.which(binary)
    if not path:
        return None
    try:
        real = Path(os.path.realpath(path))
        for parent in [real, *real.parents][:6]:
            pkg = parent / "package.json"
            if pkg.is_file():
                data = json.loads(pkg.read_text(encoding="utf-8"))
                ver = data.get("version")
                return str(ver) if ver else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return None


def _pinned_version(backend: str) -> str | None:
    if backend == "pi":
        from tianshu.executor.keqing.pi_wire import PINNED_PI_VERSION

        return PINNED_PI_VERSION
    return None


def _capabilities(backend: str) -> dict | None:
    if backend == "pi":
        from tianshu.executor.keqing.pi_adapter import PiSessionAdapter

        c = PiSessionAdapter.capabilities
        return {
            "permission_shaping": c.permission_shaping,
            "hooks": c.hooks,
            "stop_gate": c.stop_gate,
            "session_resume": c.session_resume,
            "interject": c.interject,
            "usage_reporting": c.usage_reporting,
        }
    return None  # 单发档(claude-code/codex)能力见 manifest,本页只展示会话客卿能力声明


@keqing_router.get("/keqing/status")
def get_keqing_status(request: Request):
    """客卿健康体检(只读):安装/版本漂移/能力声明/凭证来源。

    客卿=外臣:本页展示的是「外聘人才」的能力与治理状态,**不含**人格/京察/自进化
    (那是百官品类)。不读/不存 raw 凭证明文,凭证栏只报来源。
    """
    cm = request.app.state.config_manager
    gateway_enabled = bool(getattr(cm.agent_config, "keqing_gateway_enabled", False))
    backends = []
    for name in list_adapters():
        binary = _BACKEND_BINARY.get(name, name)
        installed_version = _detect_installed_version(binary)
        pinned = _pinned_version(name)
        drift = (
            installed_version is not None
            and pinned is not None
            and installed_version != pinned
        )
        # 客卿=外臣,自管凭证(自己 pi /login 或本地 CLI 配置);天枢**不管**客卿凭证——
        # 唯一例外是开启凭证网关(天枢托管 scoped token)。故凭证来源只两态:
        # 客卿自管(默认,天枢不碰)/ 网关托管(天枢注入 scoped token)。
        credential_status = "gateway" if gateway_enabled else "self-managed"
        backends.append(
            {
                "id": f"keqing:{name}",
                "backend": name,
                "binary": binary,
                "installed": installed_version is not None,
                "installed_version": installed_version,
                "pinned_version": pinned,
                "version_drift": drift,
                "capabilities": _capabilities(name),
                "credential_status": credential_status,
            }
        )
    return ApiResponse(
        success=True,
        data={"backends": backends, "gateway_enabled": gateway_enabled},
    )


@keqing_router.get("/edicts/{edict_id}/snapshots")
def list_snapshots(request: Request, edict_id: str):
    storage = request.app.state.storage
    return ApiResponse(success=True, data=storage.list_shadow_snapshots(edict_id))


class RevertRequest(BaseModel):
    sha: str


@keqing_router.post("/edicts/{edict_id}/snapshots/revert")
async def revert_snapshot(request: Request, edict_id: str, body: RevertRequest):
    from tianshu.executor.shadow_snapshot import ShadowSnapshot

    storage = request.app.state.storage
    work_tree = storage.get_shadow_work_tree(edict_id)
    if not work_tree:
        return ApiResponse(success=False, data=None, error="no shadow snapshots for this edict")

    from pathlib import Path

    shadow = ShadowSnapshot(Path(work_tree), edict_id)
    ok = shadow.revert(body.sha)
    if not ok:
        return ApiResponse(success=False, data=None, error="revert failed (see logs)")

    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        from tianshu.models.events import make_event

        await bus.emit(
            make_event(
                "shadow.reverted",
                edict_id=edict_id,
                producer="keqing",
                payload={"sha": body.sha},
            )
        )
    return ApiResponse(success=True, data={"reverted_to": body.sha})
