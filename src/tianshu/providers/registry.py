"""ModelProviderRegistry —— 模型供应商实例的 CRUD 与 key 解析门面。

三层职责边界：
- 静态身份（协议 / litellm 前缀 / 默认端点 / env 名 / 目录映射）在 profiles.py；
- 用户差异（base_url 覆盖 / key 引用 / 启停）在 model_providers 表；
- key 密文在 network_credentials(kind='llm_provider')，经 SecretVault 加密。

key 引用（api_key_ref）三态：
- ``''``          → 落 profile.key_env 的环境变量（env 回落）
- ``'credential'`` → network_credentials 加密凭证
- ``'$ENV:NAME'``  → 显式指定环境变量名（pi resolve-config-value 简化版）
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from tianshu.providers.model_catalog import CatalogModel, ModelCatalog
from tianshu.providers.profiles import (
    BUILTIN_PROFILES,
    CUSTOM_PROFILE_ID,
    ProviderProfile,
    custom_profile,
    get_profile,
)
from tianshu.secrets.models import CredentialCreate, CredentialUpdate

if TYPE_CHECKING:
    from tianshu.secrets.store import CredentialStore
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ENV_REF_PREFIX = "$ENV:"


class ModelProviderRegistry:
    def __init__(
        self,
        storage: Storage,
        catalog: ModelCatalog,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._storage = storage
        self._catalog = catalog
        self._credential_store = credential_store

    # --- profile 视图 ---

    @staticmethod
    def list_profiles() -> tuple[ProviderProfile, ...]:
        return BUILTIN_PROFILES

    def profile_for(self, row: dict) -> ProviderProfile:
        return get_profile(row.get("profile_id", "")) or custom_profile()

    # --- provider CRUD ---

    def list_providers(self) -> list[dict]:
        return [self._to_view(row) for row in self._storage.list_model_providers()]

    def get_provider(self, provider_id: str) -> dict | None:
        row = self._storage.get_model_provider(provider_id)
        return self._to_view(row) if row else None

    def get_provider_row(self, provider_id: str) -> dict | None:
        return self._storage.get_model_provider(provider_id)

    def create_provider(
        self,
        profile_id: str,
        provider_id: str = "",
        display_name: str = "",
        base_url: str = "",
        api_key: str = "",
    ) -> dict:
        profile = get_profile(profile_id)
        if profile is None:
            raise ValueError(f"unknown profile: {profile_id}")
        if profile.id == CUSTOM_PROFILE_ID and not base_url.strip():
            raise ValueError("custom provider requires base_url")
        provider_id = (provider_id or profile.id).strip().lower()
        if not _PROVIDER_ID_RE.fullmatch(provider_id):
            raise ValueError(f"invalid provider id: {provider_id!r}")
        if self._storage.get_model_provider(provider_id) is not None:
            raise ValueError(f"provider '{provider_id}' already exists")
        self._storage.save_model_provider(
            {
                "id": provider_id,
                "profile_id": profile.id,
                "display_name": display_name.strip(),
                "base_url": base_url.strip(),
                "api_key_ref": "",
                "enabled": True,
            }
        )
        if api_key.strip():
            self.set_key(provider_id, api_key.strip())
        view = self.get_provider(provider_id)
        assert view is not None
        return view

    def update_provider(
        self,
        provider_id: str,
        display_name: str | None = None,
        base_url: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        row = self._storage.get_model_provider(provider_id)
        if row is None:
            raise KeyError(f"provider '{provider_id}' not found")
        profile = self.profile_for(row)
        if base_url is not None and profile.id == CUSTOM_PROFILE_ID and not base_url.strip():
            raise ValueError("custom provider requires base_url")
        self._storage.save_model_provider(
            {
                **row,
                "display_name": (
                    display_name.strip() if display_name is not None else row["display_name"]
                ),
                "base_url": base_url.strip() if base_url is not None else row["base_url"],
                "enabled": bool(enabled) if enabled is not None else bool(row["enabled"]),
            }
        )
        view = self.get_provider(provider_id)
        assert view is not None
        return view

    def delete_provider(self, provider_id: str) -> None:
        row = self._storage.get_model_provider(provider_id)
        if row is None:
            raise KeyError(f"provider '{provider_id}' not found")
        in_use = self._storage.count_llm_configs_by_provider(provider_id)
        if in_use:
            raise ValueError(f"provider '{provider_id}' is referenced by {in_use} LLM config(s)")
        self._delete_credential(provider_id)
        self._storage.delete_model_provider(provider_id)

    # --- key 管理 ---

    def set_key(self, provider_id: str, api_key: str) -> dict:
        """写入/切换 key 引用；空串 = 清除（回落 profile.key_env 环境变量）。"""
        row = self._storage.get_model_provider(provider_id)
        if row is None:
            raise KeyError(f"provider '{provider_id}' not found")
        api_key = api_key.strip()
        if not api_key:
            self._delete_credential(provider_id)
            ref = ""
        elif api_key.startswith(_ENV_REF_PREFIX):
            env_name = api_key[len(_ENV_REF_PREFIX) :].strip()
            if not env_name:
                raise ValueError("empty env var name in $ENV: reference")
            self._delete_credential(provider_id)
            ref = f"{_ENV_REF_PREFIX}{env_name}"
        else:
            store = self._credential_store
            if store is None:
                raise ValueError(
                    "存储明文 key 需要设置 TIANSHU_SECRET_MASTER_KEY（SecretVault 未就绪）；"
                    "或改用 $ENV:VAR_NAME 引用环境变量"
                )
            existing = store.find_for_provider(provider_id, kind="llm_provider")
            if existing is not None:
                store.update(existing.id, CredentialUpdate(value=api_key))
            else:
                store.create(
                    CredentialCreate(
                        name=f"llm:{provider_id}",
                        value=api_key,
                        kind="llm_provider",
                        provider_name=provider_id,
                    )
                )
            ref = "credential"
        self._storage.save_model_provider({**row, "api_key_ref": ref})
        view = self.get_provider(provider_id)
        assert view is not None
        return view

    def _delete_credential(self, provider_id: str) -> None:
        store = self._credential_store
        if store is None:
            return
        existing = store.find_for_provider(provider_id, kind="llm_provider")
        if existing is not None:
            store.delete(existing.id)

    def resolve_key(self, provider_id: str) -> tuple[str, str]:
        """返回 (api_key, source)；source ∈ {'vault','env','none'}。解密失败回落 env。"""
        row = self._storage.get_model_provider(provider_id)
        if row is None:
            return "", "none"
        profile = self.profile_for(row)
        ref = row.get("api_key_ref", "")
        if ref == "credential" and self._credential_store is not None:
            cred = self._credential_store.find_for_provider(provider_id, kind="llm_provider")
            if cred is not None:
                try:
                    return self._credential_store.decrypt_value(cred), "vault"
                except Exception:
                    logger.exception(
                        "decrypt llm provider '%s' key failed, falling back to env", provider_id
                    )
        if ref.startswith(_ENV_REF_PREFIX):
            key = os.getenv(ref[len(_ENV_REF_PREFIX) :], "")
            return (key, "env") if key else ("", "none")
        if profile.key_env:
            key = os.getenv(profile.key_env, "")
            if key:
                return key, "env"
        return "", "none"

    # --- 解析辅助 ---

    def effective_base_url(self, provider_id: str) -> str:
        row = self._storage.get_model_provider(provider_id)
        if row is None:
            return ""
        profile = self.profile_for(row)
        return row.get("base_url") or profile.default_base_url

    def models_for(self, provider_id: str) -> list[CatalogModel]:
        row = self._storage.get_model_provider(provider_id)
        if row is None:
            return []
        return self._catalog.models_for(self.profile_for(row))

    def pricing_cny(self, provider_id: str, model_id: str) -> tuple[float, float, float] | None:
        row = self._storage.get_model_provider(provider_id)
        if row is None:
            return None
        return self._catalog.pricing_cny(self.profile_for(row), model_id)

    def catalog(self) -> ModelCatalog:
        return self._catalog

    # --- 连通性测试 ---

    async def test_connectivity(self, provider_id: str, model_id: str) -> dict:
        """直连（不走 Router）发一次 1-token chat；返回 {ok, latency_ms, error}。"""
        import time

        from tianshu.llm import LLMClient

        row = self._storage.get_model_provider(provider_id)
        if row is None:
            raise KeyError(f"provider '{provider_id}' not found")
        profile = self.profile_for(row)
        key, _source = self.resolve_key(provider_id)
        client = LLMClient(
            model=model_id,
            api_key=key,
            api_base=self.effective_base_url(provider_id),
            max_retries=0,
            temperature=0.0,
            max_tokens=1,
            timeout=30,
            provider_name=provider_id,
            litellm_prefix=profile.litellm_prefix,
        )
        start = time.monotonic()
        try:
            await client.chat([{"role": "user", "content": "ping"}])
        except Exception as exc:  # noqa: BLE001 - 测试端点即为呈现错误
            return {
                "ok": False,
                "latency_ms": int((time.monotonic() - start) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000), "error": None}

    # --- 视图 ---

    def _to_view(self, row: dict) -> dict:
        profile = self.profile_for(row)
        key, source = self.resolve_key(row["id"])
        return {
            "id": row["id"],
            "profile_id": row["profile_id"],
            "display_name": row.get("display_name") or profile.display_name,
            "base_url": row.get("base_url", ""),
            "effective_base_url": row.get("base_url") or profile.default_base_url,
            "api_key_ref": row.get("api_key_ref", ""),
            "key_source": source,
            "key_masked": _mask_key(key),
            "enabled": bool(row.get("enabled", 1)),
            "billing": profile.billing,
            "api_protocol": profile.api_protocol,
            "key_env": profile.key_env,
            "has_catalog": bool(profile.models_dev_id),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]
