"""Feishu webhook 安全：签名 / token / allowlist / dedup 单元测试。"""

from __future__ import annotations

import hashlib

import pytest

from tianshu.gateway.feishu.security import (
    DedupChecker,
    is_allowed_user,
    verify_signature,
    verify_token,
)


def test_verify_signature_skipped_when_no_key():
    """encrypt_key 为空 → 直接放行（dev 模式）。"""
    assert verify_signature({}, b"{}", "") is True


def test_verify_signature_valid():
    key = "secret_key"
    body = b'{"hello":"world"}'
    timestamp = "1700000000"
    nonce = "abc"
    payload = f"{timestamp}{nonce}{key}".encode() + body
    sig = hashlib.sha256(payload).hexdigest()
    headers = {
        "x-lark-request-timestamp": timestamp,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": sig,
    }
    assert verify_signature(headers, body, key) is True


def test_verify_signature_invalid_returns_false():
    key = "secret_key"
    body = b"{}"
    headers = {
        "x-lark-request-timestamp": "1",
        "x-lark-request-nonce": "n",
        "x-lark-signature": "deadbeef",
    }
    assert verify_signature(headers, body, key) is False


def test_verify_signature_missing_headers_returns_false():
    assert verify_signature({}, b"{}", "key") is False


def test_verify_token_skipped_when_empty():
    assert verify_token({}, "") is True


def test_verify_token_match_in_header():
    payload = {"header": {"token": "T"}}
    assert verify_token(payload, "T") is True


def test_verify_token_match_top_level_legacy():
    payload = {"token": "T"}
    assert verify_token(payload, "T") is True


def test_verify_token_mismatch():
    assert verify_token({"header": {"token": "X"}}, "T") is False


def test_is_allowed_user():
    assert is_allowed_user("ou_a", ["ou_a", "ou_b"]) is True
    assert is_allowed_user("ou_c", ["ou_a", "ou_b"]) is False


def test_is_allowed_user_empty_list_allows_all():
    """空 allowlist 表示放行任意人（hermes 一致行为）。"""
    assert is_allowed_user("ou_anybody", []) is True
    assert is_allowed_user("", ()) is True


@pytest.mark.asyncio
async def test_dedup_checker_first_seen_then_duplicate(storage):
    d = DedupChecker(storage, max_entries=10)
    assert d.check_and_mark("evt_1") is True
    assert d.check_and_mark("evt_1") is False  # 重复
    assert d.check_and_mark("evt_2") is True


@pytest.mark.asyncio
async def test_dedup_checker_empty_id_passes(storage):
    """无 message_id 直接放行（不缓存）。"""
    d = DedupChecker(storage)
    assert d.check_and_mark("") is True
    assert d.check_and_mark("") is True
