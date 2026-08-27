"""Storage / EventBus / HookRegistry 装配。

对应原 `tianshu.app.lifespan()` 中相邻的三个分区注释：
`# --- Storage ---` / `# --- EventBus ---` / `# --- HookRegistry ---`。
三者顺序相邻且无跨区变量穿线问题，合并为一个 wiring 函数。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from tianshu.application.event_history import EventHistoryConsumer
from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.evidence.service import ArtifactStore, EvidenceService
from tianshu.executor.capabilities import get_executor_manifest
from tianshu.executor.git_backend import GitBackend
from tianshu.executor.workspace_service import WorkspaceService
from tianshu.governance.decision_service import DecisionService
from tianshu.kernel.hooks import HookRegistry
from tianshu.models.workspace_policy import validate_workspace_roots
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

WORKSPACE_DIR_SETTING_KEY = "workspace_dir"


def _apply_persisted_workspace_dir(settings: TianshuSettings, storage: Storage) -> None:
    """用 app_settings 里用户配的 workspace 覆盖 env 默认（持久优先）。

    与 agent_config 同一语义。只在启动时读一次：workspace_dir 在 8 处 wiring
    被固化（工具注册时闭包捕获了路径），运行期改值不会传导到已注册的工具，
    故网页改完需重启——界面已如此标注。
    """
    try:
        value = storage.get_app_setting(WORKSPACE_DIR_SETTING_KEY)
    except Exception:  # noqa: BLE001 - 表缺失/损坏时保持 env 默认
        logger.exception("[workspace] load workspace_dir from app_settings failed")
        return
    if isinstance(value, str) and value.strip():
        settings.workspace_dir = value.strip()


def wire_storage(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 Storage / EventBus / HookRegistry 并挂载到 app.state。"""
    # --- Storage ---
    # 先于 workspace 校验建库：用户在网页上配的 workspace 存在 app_settings 里，
    # 得先能读库才知道最终值。
    storage = Storage(settings.db_path)
    try:
        storage.init_db()

        _apply_persisted_workspace_dir(settings, storage)
        workspace_roots = validate_workspace_roots(
            settings.workspace_dir,
            settings.workspace_staging_root,
        )
        app.state.workspace_roots = workspace_roots

        app.state.storage = storage
        app.state.decision_service = DecisionService(storage)
        app.state.artifact_store = ArtifactStore(
            settings.artifact_dir,
            storage.artifact_repo,
            storage.unit_of_work,
            max_object_bytes=settings.artifact_max_bytes,
            max_total_bytes=settings.artifact_quota_bytes,
        )
        app.state.evidence_service = EvidenceService(
            storage,
            app.state.artifact_store,
            executor_manifest_provider=get_executor_manifest,
        )

        app.state.workspace_service = WorkspaceService(
            storage,
            GitBackend(),
            workspace_roots.staging,
            app.state.decision_service,
        )

        # --- EventBus ---
        event_bus = EventBus()
        event_history = EventHistoryConsumer(storage)
        event_bus.on(
            "*",
            event_history,
            consumer_name=event_history.consumer_name,
            priority=0,
        )
        app.state.event_bus = event_bus
        event_bus.on(
            "decision.resolved",
            app.state.workspace_service.handle_decision_resolved,
            consumer_name="workspace_service.governed_apply_projection.v1",
        )

        # --- HookRegistry ---
        hook_registry = HookRegistry()
        app.state.hook_registry = hook_registry
    except BaseException:
        storage.close()
        raise
