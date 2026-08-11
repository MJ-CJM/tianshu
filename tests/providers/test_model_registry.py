"""统一模型注册表：迁移存量数据、model_ref 文法、目录定价、key 解析、写穿。"""

from __future__ import annotations

import sqlite3

import pytest
from cryptography.fernet import Fernet

from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.providers.model_catalog import ModelCatalog
from tianshu.providers.model_ref import normalize_for_allowlist, parse_model_ref
from tianshu.providers.profiles import (
    get_profile,
    match_profile_by_base_url,
    match_profile_for_config,
)
from tianshu.providers.registry import ModelProviderRegistry
from tianshu.secrets.store import CredentialStore
from tianshu.secrets.vault import SecretVault, reset_vault
from tianshu.storage import Storage
from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import MIGRATIONS


@pytest.fixture
def master_key_env(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", key)
    reset_vault()
    yield key
    reset_vault()


@pytest.fixture
def storage(tmp_path):
    st = Storage(str(tmp_path / "registry.db"))
    st.init_db()
    yield st
    st.close()


def _registry(storage: Storage, master_key: str) -> ModelProviderRegistry:
    vault = SecretVault(master_key)
    return ModelProviderRegistry(storage, ModelCatalog(), CredentialStore(storage, vault))


# --- 迁移存量数据（0019 回填 + 0020 加密重建）---


def _v18_conn_with_legacy_configs(rows: list[tuple[str, str, str, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn, tuple(m for m in MIGRATIONS if m.version <= 18))  # 旧库形状（明文列在）
    for name, model, api_key, api_base in rows:
        conn.execute(
            "INSERT INTO llm_configs (name, model, api_key, api_base, is_active, created_at) "
            "VALUES (?, ?, ?, ?, 0, 'now')",
            (name, model, api_key, api_base),
        )
    conn.commit()
    return conn


def test_migration_backfills_provider_and_encrypts_keys(master_key_env):
    conn = _v18_conn_with_legacy_configs(
        [
            ("ds", "deepseek-chat", "sk-ds", "https://api.deepseek.com"),
            ("gpt", "gpt-4o-mini", "sk-oa", ""),
            ("relay", "my-model", "sk-relay", "https://llm.example.com/v1"),
        ]
    )
    # 从 v18 升到最新：期望值随 MIGRATIONS 尾部自适应，
    # 免去每次追加迁移都要在此补一个数字。
    assert apply_migrations(conn, MIGRATIONS) == tuple(range(19, MIGRATIONS[-1].version + 1))

    cols = [r[1] for r in conn.execute("PRAGMA table_info(llm_configs)")]
    assert "api_key" not in cols and "provider_id" in cols

    rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM llm_configs")}
    assert rows["ds"]["provider_id"] == "deepseek"
    assert rows["gpt"]["provider_id"] == "openai"
    assert rows["relay"]["provider_id"].startswith("custom-")

    providers = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM model_providers")}
    # 与 profile 默认端点一致 → 不存差异；custom 必存端点
    assert providers["deepseek"]["base_url"] == ""
    assert providers[rows["relay"]["provider_id"]]["base_url"] == "https://llm.example.com/v1"
    assert all(p["api_key_ref"] == "credential" for p in providers.values())

    creds = conn.execute(
        "SELECT provider_name FROM network_credentials WHERE kind='llm_provider'"
    ).fetchall()
    assert {c[0] for c in creds} == set(providers)
    conn.close()


def test_migration_same_profile_different_keys_get_distinct_providers(master_key_env):
    conn = _v18_conn_with_legacy_configs(
        [
            ("a", "deepseek-chat", "sk-1", "https://api.deepseek.com"),
            ("b", "deepseek-reasoner", "sk-2", "https://api.deepseek.com"),
        ]
    )
    apply_migrations(conn, MIGRATIONS)
    rows = {r["name"]: r["provider_id"] for r in conn.execute("SELECT * FROM llm_configs")}
    assert rows["a"] != rows["b"]  # key 不同 → 各自 provider，互不覆盖
    conn.close()


def test_migration_without_keys_needs_no_vault(monkeypatch):
    monkeypatch.delenv("TIANSHU_SECRET_MASTER_KEY", raising=False)
    reset_vault()
    conn = _v18_conn_with_legacy_configs([("empty", "gpt-4o-mini", "", "")])
    # 从 v18 升到最新：期望值随 MIGRATIONS 尾部自适应，
    # 免去每次追加迁移都要在此补一个数字。
    assert apply_migrations(conn, MIGRATIONS) == tuple(range(19, MIGRATIONS[-1].version + 1))
    conn.close()
    reset_vault()


def test_migration_with_keys_but_no_vault_fails_loudly(monkeypatch):
    monkeypatch.delenv("TIANSHU_SECRET_MASTER_KEY", raising=False)
    reset_vault()
    conn = _v18_conn_with_legacy_configs([("k", "gpt-4o-mini", "sk-x", "")])
    with pytest.raises(MigrationExecutionError, match="0020_encrypt_llm_config_keys") as excinfo:
        apply_migrations(conn, MIGRATIONS)
    assert "TIANSHU_SECRET_MASTER_KEY" in str(excinfo.value.__cause__)
    # 事务回滚：明文行仍在（不会半态丢 key）
    assert conn.execute("SELECT api_key FROM llm_configs").fetchone()[0] == "sk-x"
    conn.close()
    reset_vault()


# --- model_ref 文法 ---


def test_parse_model_ref_thinking_suffix_only_in_enum():
    ref = parse_model_ref("zai-coding-cn/glm-4.6:high")
    assert (ref.provider_id, ref.model_id, ref.thinking) == ("zai-coding-cn", "glm-4.6", "high")
    # 模型 id 自带冒号（非枚举值）不剥离
    ref = parse_model_ref("ollama/qwen3:32b")
    assert (ref.provider_id, ref.model_id, ref.thinking) == ("ollama", "qwen3:32b", "")
    assert parse_model_ref("").model_id == ""


def test_normalize_for_allowlist_strips_thinking():
    assert normalize_for_allowlist("anthropic/claude-opus-4-5:high") == "anthropic/claude-opus-4-5"
    assert normalize_for_allowlist("ollama/qwen3:32b") == "ollama/qwen3:32b"


# --- profile 匹配 ---


def test_profile_matching_prefers_longer_alias():
    assert match_profile_by_base_url("https://api.minimaxi.com/anthropic").id == "minimax-coding"
    assert match_profile_by_base_url("https://api.minimaxi.com/v1").id == "minimax"
    assert match_profile_by_base_url("https://totally-unknown.example.com") is None


def test_profile_matching_for_config_falls_through():
    assert match_profile_for_config("anthropic/claude-x", "").id == "anthropic"
    assert match_profile_for_config("glm-4.6", "").id == "zhipu"
    assert match_profile_for_config("mystery-model", "") is None


# --- 目录与定价 ---


def test_catalog_pricing_subscription_is_zero():
    catalog = ModelCatalog()
    zhipu_coding = get_profile("zhipu-coding")
    assert catalog.pricing_cny(zhipu_coding, "glm-4.6") == (0.0, 0.0, 0.0)


def test_catalog_pricing_by_model_hits_snapshot():
    catalog = ModelCatalog()
    pricing = catalog.pricing_cny_by_model("deepseek/deepseek-chat")
    assert pricing is not None
    miss, hit, out = pricing
    assert 0 < hit < miss < out


# --- registry key 解析与写穿 ---


def test_registry_key_three_states(storage, master_key_env, monkeypatch):
    registry = _registry(storage, master_key_env)
    registry.create_provider(profile_id="deepseek")

    # '' → profile.key_env 环境变量回落
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    assert registry.resolve_key("deepseek") == ("sk-from-env", "env")

    # '$ENV:NAME' → 显式引用
    monkeypatch.setenv("MY_DS_KEY", "sk-explicit")
    registry.set_key("deepseek", "$ENV:MY_DS_KEY")
    assert registry.resolve_key("deepseek") == ("sk-explicit", "env")

    # 字面量 → vault 加密凭证
    registry.set_key("deepseek", "sk-literal")
    assert registry.resolve_key("deepseek") == ("sk-literal", "vault")

    # 清除 → 回落 env
    registry.set_key("deepseek", "")
    assert registry.resolve_key("deepseek") == ("sk-from-env", "env")


def test_registry_delete_refuses_when_config_references(storage, master_key_env):
    registry = _registry(storage, master_key_env)
    cm = ConfigManager(
        LLMConfigState(
            name="ds", model="deepseek-chat", api_key="sk-1", api_base="https://api.deepseek.com"
        ),
        storage=storage,
        model_registry=registry,
    )
    assert cm.state.provider_id == "deepseek"
    with pytest.raises(ValueError, match="referenced"):
        registry.delete_provider("deepseek")


def test_provider_manager_injects_effective_base_and_prefix(storage, master_key_env):
    """配置关联 provider 且 api_base 留空时，客户端必须拿到 provider 端点与
    litellm 前缀——否则裸模型串会被 litellm 打到官方默认端点（GLM key 发往
    api.openai.com 的 401 回归场景）。"""
    from tianshu.providers.manager import ProviderManager

    registry = _registry(storage, master_key_env)
    cm = ConfigManager(
        LLMConfigState(
            name="glm",
            model="glm-4.6",
            api_key="sk-glm",
            api_base="https://open.bigmodel.cn/api/paas/v4",
        ),
        storage=storage,
        model_registry=registry,
    )
    # 模拟 web「选供应商后 api_base 留空」的新建路径
    cm.add_config(
        LLMConfigState(
            name="glm-bare", model="glm-4.7", api_key="", api_base="", provider_id="zhipu"
        )
    )
    pm = ProviderManager(storage=storage, config_manager=cm, model_registry=registry)
    client = pm.get_client(config_name_override="glm-bare")
    assert client._api_base == "https://open.bigmodel.cn/api/paas/v4"
    assert client._model == "openai/glm-4.7"
    assert client._api_key == "sk-glm"  # key 复用 provider 的加密凭证


def test_config_manager_write_through_and_resolution(storage, master_key_env):
    registry = _registry(storage, master_key_env)
    cm = ConfigManager(
        LLMConfigState(
            name="seed",
            model="glm-4.6",
            api_key="sk-glm",
            api_base="https://open.bigmodel.cn/api/paas/v4",
        ),
        storage=storage,
        model_registry=registry,
    )
    state = cm.state
    assert state.provider_id == "zhipu"
    assert state.api_key == "sk-glm"
    # key 不落 llm_configs（明文列已删），落加密凭证
    row = storage.get_llm_config("seed")
    assert "api_key" not in row
    assert registry.resolve_key("zhipu") == ("sk-glm", "vault")

    # 显式清空 key（更新写面）→ provider key 引用清除
    cm.update_config("seed", api_key="")
    assert registry.resolve_key("zhipu")[1] != "vault"

    # 重建 ConfigManager（模拟重启）：key 从注册表解析回内存
    cm.update_config("seed", api_key="sk-glm-2")
    cm2 = ConfigManager(
        LLMConfigState(name="unused", model="x", api_key=""),
        storage=storage,
        model_registry=registry,
    )
    assert cm2.get_config("seed").api_key == "sk-glm-2"
