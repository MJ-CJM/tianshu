"""WorkerPool 与 WorkspaceService 的 readiness 生命周期属性（G1.5）。"""

from __future__ import annotations

import pytest

from tianshu.executor.worker_pool import WorkerPool
from tianshu.executor.workspace_service import WorkspaceService


@pytest.mark.asyncio
async def test_worker_pool_ready_until_shutdown():
    pool = WorkerPool(max_concurrency=1)
    assert pool.is_ready
    await pool.shutdown()
    assert not pool.is_ready


def test_workspace_service_ready_reflects_closing_flag():
    service = WorkspaceService.__new__(WorkspaceService)
    service._closing = False
    assert service.is_ready
    service._closing = True
    assert not service.is_ready
