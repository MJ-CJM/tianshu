"""SessionAnchor 单元测试。"""

from __future__ import annotations

import pytest

from tianshu.gateway.core.session_anchor import SessionAnchor


@pytest.mark.asyncio
async def test_anchor_get_returns_none_when_unset(storage):
    a = SessionAnchor(storage)
    assert a.get("oc_x") is None


@pytest.mark.asyncio
async def test_anchor_set_then_get(storage):
    a = SessionAnchor(storage)
    a.set("oc_x", "edict_1")
    assert a.get("oc_x") == "edict_1"
    # 覆盖
    a.set("oc_x", "edict_2")
    assert a.get("oc_x") == "edict_2"
    # 不同 chat 互不影响
    assert a.get("oc_y") is None
