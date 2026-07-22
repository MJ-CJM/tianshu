"""Feishu webhook 安全：签名 / token / allowlist / dedup。"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass

from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_SIGNATURE_FRESHNESS_SECONDS = 300


@dataclass(frozen=True)
class SecurityConfig:
    encrypt_key: str
    verification_token: str
    allowed_users: frozenset[str]
    dedup_cache_size: int


def verify_signature(
    headers: dict[str, str],
    body_bytes: bytes,
    encrypt_key: str,
    *,
    now: float | None = None,
) -> bool:
    """SHA256(timestamp + nonce + encrypt_key + body) == X-Lark-Signature。
    签名时间与本机时间相差不得超过五分钟；encrypt_key 为空时跳过校验（dev 模式）。"""
    if not encrypt_key:
        return True
    timestamp = headers.get("x-lark-request-timestamp") or headers.get(
        "X-Lark-Request-Timestamp", ""
    )
    nonce = headers.get("x-lark-request-nonce") or headers.get("X-Lark-Request-Nonce", "")
    expected_sig = headers.get("x-lark-signature") or headers.get("X-Lark-Signature", "")
    if not (timestamp and nonce and expected_sig):
        return False
    try:
        signed_at = int(timestamp)
    except ValueError:
        return False
    current_time = time.time() if now is None else now
    if abs(current_time - signed_at) > _SIGNATURE_FRESHNESS_SECONDS:
        return False
    payload = f"{timestamp}{nonce}{encrypt_key}".encode() + body_bytes
    actual = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(actual, expected_sig)


def verify_token(payload: dict, expected_token: str) -> bool:
    """检查 payload['header']['token'] == expected_token。空 token 跳过校验。"""
    if not expected_token:
        return True
    actual = (payload.get("header") or {}).get("token", "") or payload.get("token", "")
    return hmac.compare_digest(actual, expected_token)


def is_allowed_user(open_id: str, allowed: Iterable[str]) -> bool:
    """空 allowlist = 任意人都放行（与 hermes 一致）；非空时严格检查。"""
    allowed_set = set(allowed)
    if not allowed_set:
        return True
    return open_id in allowed_set


class DedupChecker:
    """基于 SQLite 的消息 ID 去重。"""

    def __init__(
        self,
        storage: Storage,
        max_entries: int = 2048,
        instance_id: str = "feishu-default",
    ) -> None:
        self._storage = storage
        self._max = max_entries
        self._instance_id = instance_id

    def check_and_mark(self, message_id: str) -> bool:
        """True = 首见（处理）；False = 重复（丢弃）。"""
        if not message_id:
            return True
        claimed = self._storage.claim_feishu_message_seen(
            message_id,
            max_entries=self._max,
            instance_id=self._instance_id,
        )
        if not claimed:
            logger.debug("[feishu/dedup] dropped duplicate message_id=%s", message_id)
        return claimed
