"""Fernet 对称加密封装。主密钥从 TIANSHU_SECRET_MASTER_KEY 读取。"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class SecretVault:
    def __init__(self, master_key: str) -> None:
        # master_key 必须是 Fernet.generate_key() 的输出（32 字节 url-safe base64）
        self._fernet = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("credential decryption failed") from e


def encrypt_canonical_mapping(vault: SecretVault, value: Mapping[str, str]) -> bytes:
    payload = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return vault.encrypt(payload)


def decrypt_canonical_mapping(vault: SecretVault, ciphertext: bytes) -> dict[str, str]:
    try:
        value = json.loads(vault.decrypt(ciphertext))
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        raise ValueError("MCP secret mapping is invalid") from None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("MCP secret mapping is invalid")
    return value


_vault: SecretVault | None = None
_vault_lock = threading.Lock()


def get_vault() -> SecretVault | None:
    """主密钥缺失返回 None。调用方据此决定降级策略。"""
    global _vault
    if _vault is not None:
        return _vault
    key = os.getenv("TIANSHU_SECRET_MASTER_KEY")
    if not key:
        logger.warning(
            "[secrets] TIANSHU_SECRET_MASTER_KEY unset; api_request / credentials store disabled"
        )
        return None
    with _vault_lock:
        if _vault is None:
            _vault = SecretVault(key)
    return _vault


def require_mcp_vault() -> SecretVault:
    """Return the process vault or fail with a stable, redacted MCP error."""

    try:
        vault = get_vault()
    except (TypeError, ValueError) as exc:
        raise ValueError("MCP secret vault unavailable") from exc
    if vault is None:
        raise ValueError("MCP secret vault unavailable")
    return vault


def reset_vault() -> None:
    """测试用。"""
    global _vault
    with _vault_lock:
        _vault = None
