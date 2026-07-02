"""Feishu 连接层：WebSocket (Step 6) + Webhook (本步)。共享 inbound_queue。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections import OrderedDict, deque
from typing import Protocol

import lark_oapi as lark
from fastapi import APIRouter, Request, Response
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from tianshu.gateway.feishu.security import DedupChecker, verify_signature, verify_token
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Monkey patch: lark-oapi 1.5.5 ws.Client 不分派 CARD 消息类型 bug
# ---------------------------------------------------------------------
# 原 _handle_data_frame 在 elif message_type == MessageType.CARD: 直接 return，
# 导致卡片按钮点击事件不会走 EventDispatcher → _on_card 永远不触发 → 飞书 200340 超时。
# 修复：CARD 也走 do_without_validation（dispatcher_handler 已通过 _callback_processor_map
# 支持卡片回调）。
# 等 lark-oapi 升级修复后可移除此 patch。
import base64 as _base64  # noqa: E402
import http as _http  # noqa: E402
import time as _time  # noqa: E402

from lark_oapi.core.const import UTF_8 as _LARK_UTF_8  # noqa: E402
from lark_oapi.core.json import JSON as _LarkJSON  # noqa: E402
from lark_oapi.ws.client import _get_by_key as _lark_get_by_key  # noqa: E402
from lark_oapi.ws.const import (  # noqa: E402
    HEADER_BIZ_RT as _LARK_HEADER_BIZ_RT,
)
from lark_oapi.ws.const import (  # noqa: E402
    HEADER_MESSAGE_ID as _LARK_HEADER_MESSAGE_ID,
)
from lark_oapi.ws.const import (  # noqa: E402
    HEADER_SEQ as _LARK_HEADER_SEQ,
)
from lark_oapi.ws.const import (  # noqa: E402
    HEADER_SUM as _LARK_HEADER_SUM,
)
from lark_oapi.ws.const import (  # noqa: E402
    HEADER_TRACE_ID as _LARK_HEADER_TRACE_ID,
)
from lark_oapi.ws.const import (  # noqa: E402
    HEADER_TYPE as _LARK_HEADER_TYPE,
)
from lark_oapi.ws.enum import MessageType as _LarkMessageType  # noqa: E402
from lark_oapi.ws.model import Response as _LarkResponse  # noqa: E402


async def _patched_handle_data_frame(self, frame):  # type: ignore[no-untyped-def]
    """替换 lark.ws.Client._handle_data_frame：让 CARD 也走 dispatcher。"""
    hs = frame.headers
    msg_id = _lark_get_by_key(hs, _LARK_HEADER_MESSAGE_ID)
    trace_id = _lark_get_by_key(hs, _LARK_HEADER_TRACE_ID)
    sum_ = _lark_get_by_key(hs, _LARK_HEADER_SUM)
    seq = _lark_get_by_key(hs, _LARK_HEADER_SEQ)
    type_ = _lark_get_by_key(hs, _LARK_HEADER_TYPE)

    pl = frame.payload
    if int(sum_) > 1:
        pl = self._combine(msg_id, int(sum_), int(seq), pl)
        if pl is None:
            return

    message_type = _LarkMessageType(type_)
    logger.debug(
        "[feishu/ws] data frame message_type=%s msg_id=%s",
        message_type.value, msg_id,
    )

    resp = _LarkResponse(code=_http.HTTPStatus.OK)
    try:
        start = int(round(_time.time() * 1000))
        if message_type in (_LarkMessageType.EVENT, _LarkMessageType.CARD):
            # PATCH：CARD 类型也走 dispatcher（原 SDK 直接 return 是 bug）
            result = self._event_handler.do_without_validation(pl)
        else:
            return
        end = int(round(_time.time() * 1000))
        header = hs.add()
        header.key = _LARK_HEADER_BIZ_RT
        header.value = str(end - start)
        if result is not None:
            resp.data = _base64.b64encode(
                _LarkJSON.marshal(result).encode(_LARK_UTF_8),
            )
    except Exception as exc:
        logger.exception(
            "[feishu/ws] _handle_data_frame failed msg_id=%s trace=%s err=%s",
            msg_id, trace_id, exc,
        )
        resp = _LarkResponse(code=_http.HTTPStatus.INTERNAL_SERVER_ERROR)

    frame.payload = _LarkJSON.marshal(resp).encode(_LARK_UTF_8)
    await self._write_message(frame.SerializeToString())


# 应用 monkey patch（模块 import 时执行一次）
lark.ws.Client._handle_data_frame = _patched_handle_data_frame
logger.info(
    "[feishu/ws] monkey-patched lark.ws.Client._handle_data_frame "
    "to support CARD message dispatch (lark-oapi 1.5.5 bug)",
)


class FeishuConnection(Protocol):
    inbound_queue: asyncio.Queue
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class WebhookConnection:
    """Webhook 模式：挂到 FastAPI router 上。POST {webhook_path}。"""

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        inbound_queue: asyncio.Queue,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self.inbound_queue = inbound_queue
        self._dedup = DedupChecker(storage, max_entries=settings.dedup_cache_size)
        self.router = APIRouter()
        self.router.post(settings.webhook_path)(self._handle_request)
        self._rate_state: OrderedDict[str, deque[float]] = OrderedDict()
        self._RATE_WINDOW = 60.0
        self._RATE_LIMIT = 120
        self._RATE_MAX_KEYS = 4096

    async def _handle_request(self, request: Request) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        rate_key = f"{self._settings.app_id}:{self._settings.webhook_path}:{client_ip}"
        if not self._allow_rate(rate_key):
            return Response("rate limited", status_code=429)

        body_bytes = await request.body()
        if len(body_bytes) > 1024 * 1024:
            return Response("body too large", status_code=413)

        headers = {k.lower(): v for k, v in request.headers.items()}
        if not verify_signature(headers, body_bytes, self._settings.encrypt_key):
            return Response("invalid signature", status_code=401)

        try:
            payload = json.loads(body_bytes)
        except Exception:
            return Response("bad json", status_code=400)

        if payload.get("type") == "url_verification":
            return Response(
                content=json.dumps({"challenge": payload.get("challenge", "")}),
                media_type="application/json",
            )

        if not verify_token(payload, self._settings.verification_token):
            return Response("invalid token", status_code=401)

        event_id = ((payload.get("header") or {}).get("event_id")) or ""
        if event_id and not self._dedup.check_and_mark(event_id):
            return Response("ok", status_code=200)

        await self.inbound_queue.put(payload)
        return Response("ok", status_code=200)

    def _allow_rate(self, key: str) -> bool:
        """60s 滑窗 / 120 req per (app_id, path, IP)。"""
        now = time.monotonic()
        bucket = self._rate_state.get(key)
        if bucket is None:
            if len(self._rate_state) >= self._RATE_MAX_KEYS:
                self._rate_state.popitem(last=False)
            bucket = deque()
            self._rate_state[key] = bucket
        self._rate_state.move_to_end(key)
        while bucket and now - bucket[0] > self._RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= self._RATE_LIMIT:
            return False
        bucket.append(now)
        return True

    async def start(self) -> None:
        logger.info("[feishu/webhook] route registered at %s", self._settings.webhook_path)

    async def stop(self) -> None:
        pass


class WebSocketConnection:
    """lark-oapi SDK 反向长连。SDK 内部跑独立线程，事件 dispatch 回主 loop。

    注意：lark.ws.Client 没有公开的 stop API，且 SDK 内置 auto_reconnect。
    daemon thread 模式下，进程退出时 SDK 自动终止。
    """

    def __init__(
        self,
        *,
        settings: FeishuSettings,
        storage: Storage,
        inbound_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self.inbound_queue = inbound_queue
        self._loop = loop
        self._client: object | None = None
        self._thread: threading.Thread | None = None
        self._last_event_at = time.monotonic()
        self._watchdog_task: asyncio.Task | None = None

    async def start(self) -> None:
        # domain 是 string URL（不是 lark.Client 那种 builder 风格）
        domain = (
            lark.LARK_DOMAIN if self._settings.domain == "lark"
            else lark.FEISHU_DOMAIN
        )
        handler = (
            lark.EventDispatcherHandler.builder(
                self._settings.encrypt_key,
                self._settings.verification_token,
            )
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_card_action_trigger(self._on_card)
            .build()
        )
        self._client = lark.ws.Client(
            self._settings.app_id,
            self._settings.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
            domain=domain,
            auto_reconnect=True,
        )
        # client.start() 是阻塞同步方法 → daemon thread
        # ⚠️ 必须在线程内新建独立 event loop，否则 lark.ws.Client 会拿到主线程
        # uvloop（已在 running）导致 RuntimeError: this event loop is already running
        self._thread = threading.Thread(
            target=self._run_client_in_thread, daemon=True, name="feishu-ws-client",
        )
        self._thread.start()
        logger.info(
            "[feishu/ws] started (app=%s, domain=%s)",
            self._settings.app_id, self._settings.domain,
        )
        self._last_event_at = time.monotonic()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    def _run_client_in_thread(self) -> None:
        """在独立线程中以独立 event loop 跑 lark.ws.Client.start()。

        修复 Python 3.14 + uvloop 下的兼容性：
        lark_oapi.ws.client 模块用全局 `loop` 变量（import 时创建），它会抓住
        主线程的 uvloop。daemon thread 里调 client.start() 时，内部 `loop.run_until_complete`
        会发现该 loop 正在主线程 running → RuntimeError: this event loop is already running.

        修法：thread 内新建独立 loop + monkey-patch lark.ws.client 模块的 `loop`
        全局变量为本线程 loop。
        """
        import asyncio as _asyncio
        new_loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(new_loop)
        try:
            import lark_oapi.ws.client as _lark_ws_client
            _lark_ws_client.loop = new_loop  # 关键：替换模块全局 loop
        except Exception:
            logger.exception("[feishu/ws] failed to patch lark client loop")
        try:
            self._client.start()
        except Exception:
            logger.exception("[feishu/ws] client.start() crashed in thread")
        finally:
            with contextlib.suppress(Exception):
                new_loop.close()

    async def stop(self) -> None:
        # lark.ws.Client 无公开 stop API；daemon thread 随主进程退出而终止
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        logger.info("[feishu/ws] stop requested (daemon thread will exit on process termination)")

    async def _watchdog(self) -> None:
        """心跳 watchdog：长时间无事件时 logger.warning。"""
        threshold = self._settings.ws_reconnect_interval * 5
        while True:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return
            idle = time.monotonic() - self._last_event_at
            if idle > threshold:
                logger.warning(
                    "[feishu/ws] no events for %ds (>%ds threshold), possible disconnection",
                    int(idle), threshold,
                )

    def _on_message(self, event: P2ImMessageReceiveV1) -> None:
        """SDK 在独立线程触发，需要 thread-safe schedule 到主 loop。"""
        self._last_event_at = time.monotonic()
        try:
            payload = self._sdk_message_to_payload(event)
            asyncio.run_coroutine_threadsafe(
                self.inbound_queue.put(payload), self._loop,
            )
        except Exception:
            logger.exception("[feishu/ws] _on_message failed")

    def _on_card(self, event: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """SDK 卡片回调：必须返回 P2CardActionTriggerResponse。"""
        self._last_event_at = time.monotonic()
        try:
            payload = self._sdk_card_to_payload(event)
            asyncio.run_coroutine_threadsafe(
                self.inbound_queue.put(payload), self._loop,
            )
        except Exception:
            logger.exception("[feishu/ws] _on_card failed")
        # 空响应让 SDK 不更新原卡片（卡片刷新由 outbound.update_card 主动触发）
        return P2CardActionTriggerResponse({})

    @staticmethod
    def _sdk_message_to_payload(event: P2ImMessageReceiveV1) -> dict:
        """把 SDK 事件对象转成 webhook 兼容字典，让 dispatcher 处理逻辑无需感知连接模式。

        防御式访问：SDK 字段缺失时 fallback 空字符串/空列表。
        """
        ev = getattr(event, "event", None)
        if ev is None:
            return {"header": {"event_type": "im.message.receive_v1", "event_id": ""}, "event": {}}
        msg = getattr(ev, "message", None) or object()
        sender = getattr(ev, "sender", None) or object()
        sender_id = getattr(sender, "sender_id", None) or object()
        mentions_raw = getattr(msg, "mentions", None) or []
        return {
            "header": {
                "event_type": "im.message.receive_v1",
                "event_id": getattr(getattr(event, "header", None), "event_id", ""),
            },
            "event": {
                "sender": {"sender_id": {
                    "open_id": getattr(sender_id, "open_id", "") or "",
                    "user_id": getattr(sender_id, "user_id", "") or "",
                    "union_id": getattr(sender_id, "union_id", "") or "",
                }},
                "message": {
                    "message_id": getattr(msg, "message_id", "") or "",
                    "chat_id": getattr(msg, "chat_id", "") or "",
                    "chat_type": getattr(msg, "chat_type", "p2p") or "p2p",
                    "message_type": getattr(msg, "message_type", "text") or "text",
                    "content": getattr(msg, "content", "") or "",
                    "mentions": [
                        {
                            "id": {"open_id": getattr(getattr(m, "id", None), "open_id", "") or ""},
                            "name": getattr(m, "name", "") or "",
                        }
                        for m in mentions_raw
                    ],
                },
            },
        }

    @staticmethod
    def _sdk_card_to_payload(event: P2CardActionTrigger) -> dict:
        """把 SDK 卡片事件转成 webhook 兼容字典。"""
        ev = getattr(event, "event", None) or object()
        operator = getattr(ev, "operator", None) or object()
        action = getattr(ev, "action", None) or object()
        context = getattr(ev, "context", None) or object()
        return {
            "header": {
                "event_type": "card.action.trigger",
                "event_id": getattr(getattr(event, "header", None), "event_id", ""),
            },
            "event": {
                "operator": {"open_id": getattr(operator, "open_id", "") or ""},
                "action": {"value": getattr(action, "value", {}) or {}},
                "context": {"open_chat_id": getattr(context, "open_chat_id", "") or ""},
            },
        }
