"""Dispatcher：dedup / allowlist / 群门控 / 命令直发 vs 文本批处理。"""

from __future__ import annotations

import asyncio

import pytest

from tianshu.gateway.telegram.dispatcher import (
    Dispatcher,
    TelegramCallback,
    TelegramMessage,
)

from ._helpers import make_settings


def _mk_dispatcher(storage, settings):
    msgs: list[TelegramMessage] = []
    cbs: list[TelegramCallback] = []

    async def on_msg(m: TelegramMessage) -> None:
        msgs.append(m)

    async def on_cb(c: TelegramCallback) -> None:
        cbs.append(c)

    d = Dispatcher(
        settings=settings,
        storage=storage,
        message_handler=on_msg,
        callback_handler=on_cb,
    )
    return d, msgs, cbs


def _msg(text="hi", update_id="u1", chat_type="private", sender_id="1", directed=True):
    return TelegramMessage(
        update_id=update_id,
        chat_id="c1",
        chat_type=chat_type,
        sender_id=sender_id,
        text=text,
        directed=directed,
    )


@pytest.mark.asyncio
async def test_command_immediate_dispatch(storage):
    d, msgs, _ = _mk_dispatcher(storage, make_settings())
    await d.handle_message(_msg(text="/help"))
    assert len(msgs) == 1
    assert msgs[0].text == "/help"


@pytest.mark.asyncio
async def test_dedup_same_update_id(storage):
    d, msgs, _ = _mk_dispatcher(storage, make_settings())
    await d.handle_message(_msg(text="/help", update_id="dup"))
    await d.handle_message(_msg(text="/help", update_id="dup"))
    assert len(msgs) == 1  # 第二次被去重


@pytest.mark.asyncio
async def test_allowlist_rejects(storage):
    d, msgs, _ = _mk_dispatcher(storage, make_settings(allowed_users=(123,)))
    await d.handle_message(_msg(text="/help", sender_id="999", update_id="a"))
    assert msgs == []
    await d.handle_message(_msg(text="/help", sender_id="123", update_id="b"))
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_group_gating_requires_directed(storage):
    d, msgs, _ = _mk_dispatcher(storage, make_settings())
    # 群里未 @bot → 跳过
    await d.handle_message(_msg(text="/help", chat_type="group", directed=False, update_id="g1"))
    assert msgs == []
    # 群里 @bot → 处理
    await d.handle_message(_msg(text="/help", chat_type="group", directed=True, update_id="g2"))
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_text_batching_merges(storage):
    d, msgs, _ = _mk_dispatcher(storage, make_settings(text_batch_delay=0.02))
    await d.handle_message(_msg(text="第一句", update_id="t1"))
    await d.handle_message(_msg(text="第二句", update_id="t2"))
    await asyncio.sleep(0.06)
    assert len(msgs) == 1
    assert msgs[0].text == "第一句\n第二句"


@pytest.mark.asyncio
async def test_callback_dispatch_and_dedup(storage):
    d, _, cbs = _mk_dispatcher(storage, make_settings())
    cb = TelegramCallback(
        update_id="cb1",
        callback_id="q1",
        chat_id="c1",
        sender_id="1",
        message_id="9",
        data="ea:approve:once:M",
    )
    await d.handle_callback(cb)
    await d.handle_callback(cb)  # 同 update_id 去重
    assert len(cbs) == 1
