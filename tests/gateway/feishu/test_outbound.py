"""FeishuOutbound 单元测试：markdown 检测、客户端调用、chat_id 反查。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.outbound import FeishuOutbound, _MD_HINT_RE
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope


def _settings(home: str = "") -> FeishuSettings:
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=("ou_test",), home_channel=home,
        encrypt_key="", verification_token="", bot_open_id="", bot_name="",
        webhook_path="/feishu/webhook", ws_reconnect_interval=120,
        text_batch_delay=0.6, dedup_cache_size=2048,
    )


def test_md_hint_regex_detects_markdown():
    assert _MD_HINT_RE.search("\n# title\n") is not None
    assert _MD_HINT_RE.search("\n- item") is not None
    assert _MD_HINT_RE.search("**bold**") is not None
    assert _MD_HINT_RE.search("plain text only") is None


@pytest.mark.asyncio
async def test_send_text_uses_post_when_markdown_detected(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.success = lambda: True
    fake_resp.data = MagicMock(message_id="msg_1")
    fake_client.im.v1.message.acreate = AsyncMock(return_value=fake_resp)
    out._client = fake_client
    mid = await out.send_text("oc_x", "**hello**")
    assert mid == "msg_1"
    assert fake_client.im.v1.message.acreate.await_count == 1


@pytest.mark.asyncio
async def test_send_text_plain_when_no_markdown(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.success = lambda: True
    fake_resp.data = MagicMock(message_id="m1")
    fake_client.im.v1.message.acreate = AsyncMock(return_value=fake_resp)
    out._client = fake_client
    mid = await out.send_text("oc_x", "plain hello")
    assert mid == "m1"
    assert fake_client.im.v1.message.acreate.await_count == 1


@pytest.mark.asyncio
async def test_send_returns_none_when_client_uninitialized(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    # 未调 start()，self._client = None
    mid = await out.send_text("oc_x", "hello")
    assert mid is None


@pytest.mark.asyncio
async def test_send_text_empty_inputs_short_circuits(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    assert await out.send_text("", "x") is None
    assert await out.send_text("oc", "") is None


@pytest.mark.asyncio
async def test_lookup_chat_id_uses_metadata(storage):
    """events handler 反查 edict.metadata.chat_id。"""
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_meta"})
    storage.save_edict(edict)
    event = EventEnvelope(event_type="execution.completed", edict_id=edict.id)
    chat_id = out._lookup_chat_id(event)
    assert chat_id == "oc_meta"


@pytest.mark.asyncio
async def test_lookup_chat_id_fallback_home_channel(storage):
    """metadata 无 chat_id → 兜底 home_channel。"""
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(home="oc_home"), storage=storage, event_bus=bus)
    edict = Edict(title="t", goal="g", source="api", metadata={})
    storage.save_edict(edict)
    event = EventEnvelope(event_type="execution.completed", edict_id=edict.id)
    chat_id = out._lookup_chat_id(event)
    assert chat_id == "oc_home"


@pytest.mark.asyncio
async def test_lookup_chat_id_returns_none_when_no_edict_no_home(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(home=""), storage=storage, event_bus=bus)
    event = EventEnvelope(event_type="execution.completed", edict_id="missing")
    assert out._lookup_chat_id(event) is None


def _fake_resp(success: bool = True, msg_id: str = "m_ok"):
    resp = MagicMock()
    resp.success = lambda: success
    resp.data = MagicMock(message_id=msg_id) if success else None
    resp.code = 0 if success else 1
    resp.msg = "ok" if success else "fail"
    return resp


@pytest.mark.asyncio
async def test_send_card_invokes_acreate(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_client.im.v1.message.acreate = AsyncMock(return_value=_fake_resp(msg_id="mc_1"))
    out._client = fake_client
    mid = await out.send_card("oc_x", {"elements": []})
    assert mid == "mc_1"


@pytest.mark.asyncio
async def test_update_card_returns_true_on_success(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_client.im.v1.message.apatch = AsyncMock(return_value=_fake_resp())
    out._client = fake_client
    ok = await out.update_card("msg_X", {"x": 1})
    assert ok is True


@pytest.mark.asyncio
async def test_update_card_false_when_client_none(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    assert await out.update_card("m", {}) is False


@pytest.mark.asyncio
async def test_update_card_false_on_failure(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_client.im.v1.message.apatch = AsyncMock(return_value=_fake_resp(success=False))
    out._client = fake_client
    assert await out.update_card("m", {}) is False


@pytest.mark.asyncio
async def test_update_card_false_when_apatch_raises(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_client.im.v1.message.apatch = AsyncMock(side_effect=RuntimeError("net"))
    out._client = fake_client
    assert await out.update_card("m", {}) is False


@pytest.mark.asyncio
async def test_send_post_falls_back_to_plain_when_post_fails(storage):
    """post 发送失败 → fallback 到 plain text。"""
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    # 第一次 acreate（post）失败，第二次（plain）成功
    fake_client.im.v1.message.acreate = AsyncMock(side_effect=[
        _fake_resp(success=False),
        _fake_resp(msg_id="plain_ok"),
    ])
    out._client = fake_client
    mid = await out.send_text("oc_x", "**bold**")
    assert mid == "plain_ok"
    assert fake_client.im.v1.message.acreate.await_count == 2


@pytest.mark.asyncio
async def test_send_returns_none_when_acreate_raises(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_client.im.v1.message.acreate = AsyncMock(side_effect=RuntimeError("net"))
    out._client = fake_client
    assert await out.send_text("oc_x", "hello") is None


@pytest.mark.asyncio
async def test_on_execution_completed_sends_post_with_table_converted(storage):
    """订阅 execution.completed → 拉 memorial.result → 发 post（v2：完整 + 表格转列表）。"""
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out._send_post = AsyncMock(return_value="m1")

    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    result_md = (
        "概况：\n\n"
        "| 项目 | 值 |\n"
        "|------|-----|\n"
        "| 标题 | demo |\n"
        "| 总页数 | 26 页 |\n\n"
        "结尾内容。"
    )
    memorial = Memorial(edict_id=edict.id, instruction="i", result=result_md)
    storage.save_memorial(memorial)
    event = EventEnvelope(
        event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id,
        payload={"title": "完成"},
    )
    await out._on_execution_completed(event)
    out._send_post.assert_awaited_once()
    chat_arg, body_arg = out._send_post.await_args.args
    assert chat_arg == "oc_x"
    # 单段直接发原文（不再加 "✅ 完成" 头）；表格已转列表
    assert "**标题**：demo" in body_arg
    assert "**总页数**：26 页" in body_arg
    assert "结尾内容" in body_arg


@pytest.mark.asyncio
async def test_on_execution_completed_prefers_final_output(storage):
    """final_output 存在时只发它，过滤掉 result 里的中间过程（规划/调研）。"""
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out._send_post = AsyncMock(return_value="m1")

    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    full_result = (
        "## t1: 调研天气 API\n\n"
        "wttr.in 接口分析...\n\n---\n\n"
        "## t2: 编写脚本\n\nshell 脚本如下...\n\n---\n\n"
        "## t3: 推送结果\n\n上海今日多云 22°C"
    )
    final = "上海今日多云 22°C，湿度 65%"
    memorial = Memorial(
        edict_id=edict.id, instruction="i",
        result=full_result, final_output=final,
    )
    storage.save_memorial(memorial)
    event = EventEnvelope(
        event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id,
    )
    await out._on_execution_completed(event)

    out._send_post.assert_awaited_once()
    body_arg = out._send_post.await_args.args[1]
    # 只发 final_output，不应出现规划/调研标题
    assert final in body_arg
    assert "## t1" not in body_arg
    assert "调研天气 API" not in body_arg
    assert "wttr.in" not in body_arg


@pytest.mark.asyncio
async def test_on_execution_completed_falls_back_to_result_when_no_final(storage):
    """final_output 为空时回退用 result（兼容老 memorial / outer-loop 场景）。"""
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out._send_post = AsyncMock(return_value="m1")

    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    memorial = Memorial(
        edict_id=edict.id, instruction="i",
        result="老 memorial 的 result", final_output=None,
    )
    storage.save_memorial(memorial)
    event = EventEnvelope(
        event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id,
    )
    await out._on_execution_completed(event)
    body_arg = out._send_post.await_args.args[1]
    assert "老 memorial 的 result" in body_arg


@pytest.mark.asyncio
async def test_on_execution_completed_long_content_split(storage):
    """超长 result 拆多段连续下发。"""
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial
    from tianshu.gateway.feishu.markdown_compat import DEFAULT_CHUNK_SIZE
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out._send_post = AsyncMock(return_value="m1")
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    long_result = "x" * (DEFAULT_CHUNK_SIZE * 2 + 100)
    memorial = Memorial(edict_id=edict.id, instruction="i", result=long_result)
    storage.save_memorial(memorial)
    event = EventEnvelope(
        event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id,
    )
    await out._on_execution_completed(event)
    assert out._send_post.await_count >= 2


@pytest.mark.asyncio
async def test_on_execution_completed_removes_typing_then_posts(storage):
    """有 typing reaction → 移除 + 紧接 post 完整内容。"""
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out.remove_reaction = AsyncMock(return_value=True)
    out._send_post = AsyncMock(return_value="m_post")

    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction="i", result="正文内容")
    storage.save_memorial(memorial)
    storage.save_feishu_thinking(
        memorial_id=memorial.id, chat_id="oc_x",
        reaction_id="rx_X", source_message_id="om_u_orig",
    )
    event = EventEnvelope(
        event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id,
    )
    await out._on_execution_completed(event)
    out.remove_reaction.assert_awaited_once_with("om_u_orig", "rx_X")
    out._send_post.assert_awaited_once()
    body_arg = out._send_post.await_args.args[1]
    assert "正文内容" in body_arg
    assert storage.pop_feishu_thinking(memorial.id) is None


@pytest.mark.asyncio
async def test_on_execution_failed_swaps_typing_to_crossmark(storage):
    """失败 → 移除 typing + 加 CrossMark + 发失败提示。"""
    from tianshu.models.edict import Edict
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out.remove_reaction = AsyncMock(return_value=True)
    out.add_reaction = AsyncMock(return_value="rx_fail")
    out.send_text = AsyncMock()
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    storage.save_feishu_thinking(
        memorial_id="mem_fail", chat_id="oc_x",
        reaction_id="rx_typing", source_message_id="om_u_fail",
    )
    event = EventEnvelope(
        event_type="execution.failed", edict_id=edict.id, memorial_id="mem_fail",
        payload={"error": "boom"},
    )
    await out._on_execution_failed(event)
    out.remove_reaction.assert_awaited_once_with("om_u_fail", "rx_typing")
    out.add_reaction.assert_awaited_once_with("om_u_fail", "CrossMark")
    out.send_text.assert_awaited_once()
    assert "boom" in out.send_text.await_args.args[1]


@pytest.mark.asyncio
async def test_on_execution_completed_skips_when_no_chat(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(home=""), storage=storage, event_bus=bus)
    out._send_post = AsyncMock()
    event = EventEnvelope(
        event_type="execution.completed", edict_id="missing", memorial_id="m",
    )
    await out._on_execution_completed(event)
    out._send_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_execution_failed_sends_error(storage):
    from tianshu.models.edict import Edict
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out.send_text = AsyncMock(return_value="m")
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    event = EventEnvelope(
        event_type="execution.failed", edict_id=edict.id, memorial_id="m",
        payload={"error": "OOM"},
    )
    await out._on_execution_failed(event)
    out.send_text.assert_awaited_once()
    args = out.send_text.await_args.args
    assert "OOM" in args[1]


@pytest.mark.asyncio
async def test_on_execution_completed_skips_no_memorial_result(storage):
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    out.send_text = AsyncMock()
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_x"})
    storage.save_edict(edict)
    # memorial.result 为空 → 不发
    memorial = Memorial(edict_id=edict.id, instruction="i", result="")
    storage.save_memorial(memorial)
    event = EventEnvelope(
        event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id,
    )
    out._send_post = AsyncMock()
    await out._on_execution_completed(event)
    out._send_post.assert_not_awaited()


def test_start_subscribes_event_handlers(storage):
    """start() 应注册 execution.completed / execution.failed 订阅。"""
    bus = MagicMock()
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    # 替 _build_client 避免真起 lark client
    out._build_client = lambda: MagicMock()
    out.start()
    assert bus.on.call_count >= 2
    types = [c.args[0] for c in bus.on.call_args_list]
    assert "execution.completed" in types
    assert "execution.failed" in types
