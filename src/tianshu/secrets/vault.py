"""Fernet 对称加密封装。主密钥从 TIANSHU_SECRET_MASTER_KEY 读取。"""

from __future__ import annotations

import logging
import os
import threading

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
            "[secrets] TIANSHU_SECRET_MASTER_KEY unset; "
            "api_request / credentials store disabled"
        )
        return None
    with _vault_lock:
        if _vault is None:
            _vault = SecretVault(key)
    return _vault


def reset_vault() -> None:
    """测试用。"""
    global _vault
    with _vault_lock:
        _vault = None
