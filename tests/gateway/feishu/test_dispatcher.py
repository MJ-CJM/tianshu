"""Dispatcher：p2p / 群@ / 卡片 / allowlist 拒绝。"""
from __future__ import annotations

import asyncio
import json

import pytest

from tianshu.gateway.feishu.dispatcher import (
    Dispatcher,
    FeishuCardAction,
    FeishuMessage,
)
from tianshu.gateway.feishu.settings import FeishuSettings


def _settings(*, allowed=("ou_test",), bot_id="ou_bot", batch_delay=0.0):
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=allowed, home_channel="",
        encrypt_key="", verification_token="", bot_open_id=bot_id, bot_name="bot",
        webhook_path="/feishu/webhook", ws_reconnect_interval=120,
        text_batch_delay=batch_delay, dedup_cache_size=2048,
    )


def _msg_event(*, event_id="evt", chat_id="oc_x", chat_type="p2p",
               sender="ou_test", text="hi", mentions=None, msg_type="text") -> dict:
    if msg_type == "text":
        content = json.dumps({"text": text})
    else:
        content = json.dumps({"content": [[{"tag": "text", "text": text}]]})
    return {
        "header": {"event_type": "im.message.receive_v1", "event_id": event_id},
        "event": {
            "sender": {"sender_id": {"open_id": sender}},
            "message": {
                "message_id": "om_1", "chat_id": chat_id, "chat_type": chat_type,
                "message_type": msg_type, "content": content,
                "mentions": mentions or [],
            },
        },
    }


def _card_event(*, event_id="evt", chat_id="oc_x", sender="ou_test", value=None) -> dict:
    return {
        "header": {"event_type": "card.action.trigger", "event_id": event_id},
        "event": {
            "operator": {"open_id": sender},
            "action": {"value": value or {"action": "approve"}},
            "context": {"open_chat_id": chat_id},
        },
    }


@pytest.fixture
async def dispatcher():
    queue: asyncio.Queue = asyncio.Queue()
    msgs: list[FeishuMessage] = []
    cards: list[FeishuCardAction] = []

    async def on_msg(m: FeishuMessage):
        msgs.append(m)

    async def on_card(c: FeishuCardAction):
        cards.append(c)

    d = Dispatcher(
        settings=_settings(),
        inbound_queue=queue,
        message_handler=on_msg,
        card_handler=on_card,
    )
    await d.start()
    yield d, queue, msgs, cards
    await d.stop()


@pytest.mark.asyncio
async def test_p2p_text_dispatched(dispatcher):
    d, queue, msgs, _ = dispatcher
    await queue.put(_msg_event(text="hello"))
    # 批处理静默期 0.0 → asyncio.create_task 调度，等一拍
    await asyncio.sleep(0.15)
    assert len(msgs) == 1
    assert msgs[0].text == "hello"
    assert msgs[0].chat_type == "p2p"


@pytest.mark.asyncio
async def test_command_skips_batching(dispatcher):
    """以 / 开头的命令直接派发（不走批处理）。"""
    d, queue, msgs, _ = dispatcher
    await queue.put(_msg_event(text="/status"))
    await asyncio.sleep(0.05)
    assert len(msgs) == 1
    assert msgs[0].text == "/status"


@pytest.mark.asyncio
async def test_text_batching_merges_consecutive(dispatcher):
    """两条紧挨的文本应合并成一条 message。"""
    d, queue, msgs, _ = dispatcher
    await queue.put(_msg_event(event_id="e1", text="part1"))
    await queue.put(_msg_event(event_id="e2", text="part2"))
    await asyncio.sleep(0.2)
    assert len(msgs) == 1
    assert "part1" in msgs[0].text
    assert "part2" in msgs[0].text


@pytest.mark.asyncio
async def test_group_without_mention_ignored(dispatcher):
    d, queue, msgs, _ = dispatcher
    # 群消息但没 @bot
    await queue.put(_msg_event(chat_type="group", text="random chat", mentions=[]))
    await asyncio.sleep(0.15)
    assert len(msgs) == 0


@pytest.mark.asyncio
async def test_group_with_mention_dispatched(dispatcher):
    d, queue, msgs, _ = dispatcher
    await queue.put(_msg_event(
        chat_type="group", text="hello",
        mentions=[{"id": {"open_id": "ou_bot"}, "name": "bot"}],
    ))
    await asyncio.sleep(0.15)
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_non_allowlist_sender_dropped(dispatcher):
    d, queue, msgs, _ = dispatcher
    await queue.put(_msg_event(sender="ou_attacker", text="x"))
    await asyncio.sleep(0.15)
    assert len(msgs) == 0


@pytest.mark.asyncio
async def test_card_event_dispatched(dispatcher):
    d, queue, _, cards = dispatcher
    await queue.put(_card_event(value={"memorial_id": "m", "action": "approve"}))
    await asyncio.sleep(0.05)
    assert len(cards) == 1
    assert cards[0].value["action"] == "approve"


@pytest.mark.asyncio
async def test_card_event_non_allowlist_dropped(dispatcher):
    d, queue, _, cards = dispatcher
    await queue.put(_card_event(sender="ou_attacker"))
    await asyncio.sleep(0.05)
    assert len(cards) == 0


@pytest.mark.asyncio
async def test_post_message_extracted(dispatcher):
    d, queue, msgs, _ = dispatcher
    await queue.put(_msg_event(text="rich text", msg_type="post"))
    await asyncio.sleep(0.15)
    assert len(msgs) == 1
    assert "rich text" in msgs[0].text


@pytest.mark.asyncio
async def test_unknown_event_type_ignored(dispatcher):
    d, queue, msgs, cards = dispatcher
    await queue.put({"header": {"event_type": "im.unknown", "event_id": "e"},
                     "event": {}})
    await asyncio.sleep(0.05)
    assert len(msgs) == 0
    assert len(cards) == 0


@pytest.mark.asyncio
async def test_empty_text_dropped(dispatcher):
    d, queue, msgs, _ = dispatcher
    await queue.put(_msg_event(text=""))
    await asyncio.sleep(0.15)
    assert len(msgs) == 0
