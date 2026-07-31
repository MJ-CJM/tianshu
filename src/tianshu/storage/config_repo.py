"""Storage Config 领域 Mixin —— LLM 配置、Provider、插件、工具开关、MCP 覆盖、引擎偏好。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime

from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked

_UPSERT_MCP_OVERRIDE = """INSERT INTO mcp_server_overrides
   (name, enabled, env_ciphertext, env_keys_json,
    tools_include_json, tools_exclude_json,
    transport, command, args_json, url,
    headers_ciphertext, header_keys_json,
    default_tier, timeout, connect_timeout, tool_overrides_json,
    updated_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
   ON CONFLICT(name) DO UPDATE SET
     enabled = COALESCE(excluded.enabled, mcp_server_overrides.enabled),
     env_ciphertext = COALESCE(excluded.env_ciphertext, mcp_server_overrides.env_ciphertext),
     env_keys_json = COALESCE(excluded.env_keys_json, mcp_server_overrides.env_keys_json),
     tools_include_json = COALESCE(excluded.tools_include_json, mcp_server_overrides.tools_include_json),
     tools_exclude_json = COALESCE(excluded.tools_exclude_json, mcp_server_overrides.tools_exclude_json),
     transport = COALESCE(excluded.transport, mcp_server_overrides.transport),
     command = COALESCE(excluded.command, mcp_server_overrides.command),
     args_json = COALESCE(excluded.args_json, mcp_server_overrides.args_json),
     url = COALESCE(excluded.url, mcp_server_overrides.url),
     headers_ciphertext = COALESCE(excluded.headers_ciphertext, mcp_server_overrides.headers_ciphertext),
     header_keys_json = COALESCE(excluded.header_keys_json, mcp_server_overrides.header_keys_json),
     default_tier = COALESCE(excluded.default_tier, mcp_server_overrides.default_tier),
     timeout = COALESCE(excluded.timeout, mcp_server_overrides.timeout),
     connect_timeout = COALESCE(excluded.connect_timeout, mcp_server_overrides.connect_timeout),
     tool_overrides_json = COALESCE(excluded.tool_overrides_json, mcp_server_overrides.tool_overrides_json),
     updated_at = excluded.updated_at"""


def _encrypt_optional_mapping(
    value: dict[str, str] | None,
) -> tuple[bytes | None, str | None]:
    from tianshu.secrets.vault import encrypt_canonical_mapping, require_mcp_vault

    if value is None:
        return None, None
    ciphertext = encrypt_canonical_mapping(require_mcp_vault(), value)
    keys_json = json.dumps(sorted(value), separators=(",", ":"), ensure_ascii=False)
    return ciphertext, keys_json


def _mcp_override_values(
    name: str,
    *,
    enabled: bool | None,
    env: dict[str, str] | None,
    tools_include: list[str] | None,
    tools_exclude: list[str] | None,
    transport: str | None,
    command: str | None,
    args: list[str] | None,
    url: str | None,
    headers: dict[str, str] | None,
    default_tier: int | None,
    timeout: int | None,
    connect_timeout: int | None,
    tool_overrides: dict[str, int] | None,
) -> tuple[object, ...]:
    now = datetime.now(UTC).isoformat()
    env_ciphertext, env_keys_json = _encrypt_optional_mapping(env)
    headers_ciphertext, header_keys_json = _encrypt_optional_mapping(headers)
    return (
        name,
        None if enabled is None else (1 if enabled else 0),
        env_ciphertext,
        env_keys_json,
        json.dumps(tools_include) if tools_include is not None else None,
        json.dumps(tools_exclude) if tools_exclude is not None else None,
        transport,
        command,
        json.dumps(args) if args is not None else None,
        url,
        headers_ciphertext,
        header_keys_json,
        default_tier,
        timeout,
        connect_timeout,
        json.dumps(tool_overrides) if tool_overrides is not None else None,
        now,
    )


class ConfigMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- LLM Configs ---

    def save_llm_config(self, config: dict) -> None:
        # api_key 不落盘：明文列已由迁移 0020 删除，key 在 network_credentials
        # (kind='llm_provider') 加密存储，经 provider_id 关联。
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO llm_configs
                   (name, model, provider_id, api_base, max_retries, temperature,
                    top_p, max_tokens, enabled, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    config["name"],
                    config["model"],
                    config.get("provider_id", ""),
                    config.get("api_base", ""),
                    config.get("max_retries", 3),
                    config.get("temperature", 0.7),
                    config.get("top_p", 1.0),
                    config.get("max_tokens", 4096),
                    1 if config.get("enabled", True) else 0,
                    1 if config.get("is_active", False) else 0,
                    config.get("created_at", datetime.now(UTC).isoformat()),
                ),
            )

    def list_llm_configs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM llm_configs ORDER BY is_active DESC, name ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_llm_config(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM llm_configs WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def delete_llm_config(self, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM llm_configs WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def set_active_llm_config(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE llm_configs SET is_active = 0")
            self._conn.execute("UPDATE llm_configs SET is_active = 1 WHERE name = ?", (name,))

    # --- Providers ---

    def save_provider(self, provider: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO providers
                   (name, model, api_base, capabilities_json, rpm_limit, tpm_limit,
                    rpm_current, tpm_current, rpm_window_start, status, priority,
                    cost_per_1k_prompt, cost_per_1k_completion, cost_per_1k_cache_read,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider["name"],
                    provider["model"],
                    provider.get("api_base"),
                    json.dumps(provider.get("capabilities", [])),
                    provider.get("rpm_limit"),
                    provider.get("tpm_limit"),
                    provider.get("rpm_current", 0),
                    provider.get("tpm_current", 0),
                    provider.get("rpm_window_start"),
                    provider.get("status", "active"),
                    provider.get("priority", 100),
                    provider.get("cost_per_1k_prompt"),
                    provider.get("cost_per_1k_completion"),
                    provider.get("cost_per_1k_cache_read"),
                    provider.get("created_at", datetime.now(UTC).isoformat()),
                ),
            )

    def get_provider(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM providers WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["capabilities"] = json.loads(d.pop("capabilities_json", "[]"))
        return d

    def list_providers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM providers ORDER BY priority ASC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["capabilities"] = json.loads(d.pop("capabilities_json", "[]"))
            result.append(d)
        return result

    def delete_provider(self, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM providers WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def update_provider(self, name: str, updates: dict) -> None:
        sets: list[str] = []
        params: list = []
        for key, value in updates.items():
            if key == "capabilities":
                sets.append("capabilities_json = ?")
                params.append(json.dumps(value))
            elif key in (
                "model",
                "api_base",
                "status",
                "rpm_limit",
                "tpm_limit",
                "priority",
                "cost_per_1k_prompt",
                "cost_per_1k_completion",
                "cost_per_1k_cache_read",
            ):
                sets.append(f"{key} = ?")
                params.append(value)
        if not sets:
            return
        params.append(name)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE providers SET {', '.join(sets)} WHERE name = ?", params)

    # --- App Settings（KV 持久化；value 统一 JSON）---

    def get_app_setting(self, key: str) -> object | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def set_app_setting(self, key: str, value: object) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO app_settings (key, value_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     value_json = excluded.value_json,
                     updated_at = excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), now),
            )

    # --- Plugins ---

    def save_plugin(self, plugin: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO plugins
                   (name, version, manifest_json, status, sha256, installed_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    plugin["name"],
                    plugin.get("version", "0.0.0"),
                    json.dumps(plugin.get("manifest", {})),
                    plugin.get("status", "manifest_only"),
                    plugin.get("sha256"),
                    plugin.get("installed_at", now),
                    now,
                ),
            )

    def list_plugins(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM plugins ORDER BY name ASC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["manifest"] = json.loads(d.pop("manifest_json", "{}"))
            result.append(d)
        return result

    def get_plugin(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM plugins WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["manifest"] = json.loads(d.pop("manifest_json", "{}"))
        return d

    def update_plugin_status(self, name: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE plugins SET status = ?, updated_at = ? WHERE name = ?",
                (status, datetime.now(UTC).isoformat(), name),
            )

    # --- tool switches ---------------------------------------------------

    def list_disabled_tools(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_name FROM tool_switches WHERE enabled = 0"
            ).fetchall()
            return {r["tool_name"] for r in rows}

    def set_tool_enabled(self, tool_name: str, enabled: bool) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO tool_switches (tool_name, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tool_name) DO UPDATE SET
                     enabled = excluded.enabled,
                     updated_at = excluded.updated_at""",
                (tool_name, 1 if enabled else 0, now),
            )

    # --- mcp server overrides --------------------------------------------

    def list_mcp_overrides(self) -> list[dict]:
        """读取所有 mcp_server_overrides 行。

        nullable 字段语义：
          * 若 YAML 中存在同名 server：NULL = 沿用 YAML，非 NULL = 覆写
          * 若 YAML 中无同名 server：DB 必须填够 transport + 主字段，merge 时晋级为完整 server
        """
        from tianshu.secrets.vault import decrypt_canonical_mapping, require_mcp_vault

        with self._lock:
            rows = self._conn.execute(
                """SELECT name, enabled, env_ciphertext,
                          tools_include_json, tools_exclude_json,
                          transport, command, args_json,
                          url, headers_ciphertext,
                          default_tier, timeout, connect_timeout,
                          tool_overrides_json
                     FROM mcp_server_overrides"""
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "name": r["name"],
                    "enabled": None if r["enabled"] is None else bool(r["enabled"]),
                    "env": (
                        decrypt_canonical_mapping(require_mcp_vault(), r["env_ciphertext"])
                        if r["env_ciphertext"] is not None
                        else None
                    ),
                    "tools_include": (
                        json.loads(r["tools_include_json"]) if r["tools_include_json"] else None
                    ),
                    "tools_exclude": (
                        json.loads(r["tools_exclude_json"]) if r["tools_exclude_json"] else None
                    ),
                    "transport": r["transport"],
                    "command": r["command"],
                    "args": json.loads(r["args_json"]) if r["args_json"] else None,
                    "url": r["url"],
                    "headers": (
                        decrypt_canonical_mapping(require_mcp_vault(), r["headers_ciphertext"])
                        if r["headers_ciphertext"] is not None
                        else None
                    ),
                    "default_tier": r["default_tier"],
                    "timeout": r["timeout"],
                    "connect_timeout": r["connect_timeout"],
                    "tool_overrides": (
                        json.loads(r["tool_overrides_json"]) if r["tool_overrides_json"] else None
                    ),
                }
            )
        return out

    def upsert_mcp_override(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        env: dict[str, str] | None = None,
        tools_include: list[str] | None = None,
        tools_exclude: list[str] | None = None,
        transport: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        default_tier: int | None = None,
        timeout: int | None = None,
        connect_timeout: int | None = None,
        tool_overrides: dict[str, int] | None = None,
    ) -> None:
        """upsert 一行 server 配置；None 字段写入 NULL（= 沿用 YAML 或不指定）。"""
        values = _mcp_override_values(
            name,
            enabled=enabled,
            env=env,
            tools_include=tools_include,
            tools_exclude=tools_exclude,
            transport=transport,
            command=command,
            args=args,
            url=url,
            headers=headers,
            default_tier=default_tier,
            timeout=timeout,
            connect_timeout=connect_timeout,
            tool_overrides=tool_overrides,
        )
        with self._lock, self._conn:
            self._conn.execute(_UPSERT_MCP_OVERRIDE, values)

    def upsert_mcp_override_with_audit(
        self,
        name: str,
        audit: AppendSystemAuditRequest,
        *,
        enabled: bool | None = None,
        env: dict[str, str] | None = None,
        tools_include: list[str] | None = None,
        tools_exclude: list[str] | None = None,
        transport: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        default_tier: int | None = None,
        timeout: int | None = None,
        connect_timeout: int | None = None,
        tool_overrides: dict[str, int] | None = None,
    ) -> None:
        values = _mcp_override_values(
            name,
            enabled=enabled,
            env=env,
            tools_include=tools_include,
            tools_exclude=tools_exclude,
            transport=transport,
            command=command,
            args=args,
            url=url,
            headers=headers,
            default_tier=default_tier,
            timeout=timeout,
            connect_timeout=connect_timeout,
            tool_overrides=tool_overrides,
        )
        with self._lock, self._conn:
            self._conn.execute(_UPSERT_MCP_OVERRIDE, values)
            _append_system_audit_unlocked(self._conn, audit)

    def delete_mcp_override(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM mcp_server_overrides WHERE name = ?", (name,))

    def delete_mcp_override_with_audit(
        self,
        name: str,
        audit: AppendSystemAuditRequest,
    ) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM mcp_server_overrides WHERE name = ?",
                (name,),
            )
            if cursor.rowcount > 0:
                _append_system_audit_unlocked(self._conn, audit)
        return cursor.rowcount > 0

    # --- engine preferences ---------------------------------------------

    def get_engine_preferences(self) -> dict:
        """返回 {fetch_chain, search_provider, fallback_mode,
        scrapling_dynamic_enabled, scrapling_stealthy_enabled};
        无记录返回全空（不覆盖 profile），开关默认 False。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetch_chain, search_provider, fallback_mode, "
                "scrapling_dynamic_enabled, scrapling_stealthy_enabled "
                "FROM engine_preferences WHERE id='default'"
            ).fetchone()
        if row is None:
            return {
                "fetch_chain": [],
                "search_provider": None,
                "fallback_mode": None,
                "scrapling_dynamic_enabled": False,
                "scrapling_stealthy_enabled": False,
            }
        chain = json.loads(row["fetch_chain"] or "[]")
        return {
            "fetch_chain": chain if isinstance(chain, list) else [],
            "search_provider": row["search_provider"],
            "fallback_mode": row["fallback_mode"],
            "scrapling_dynamic_enabled": bool(row["scrapling_dynamic_enabled"]),
            "scrapling_stealthy_enabled": bool(row["scrapling_stealthy_enabled"]),
        }

    def set_engine_preferences(
        self,
        *,
        fetch_chain: list[str],
        search_provider: str | None,
        fallback_mode: str | None,
        scrapling_dynamic_enabled: bool = False,
        scrapling_stealthy_enabled: bool = False,
    ) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO engine_preferences
                   (id, fetch_chain, search_provider, fallback_mode,
                    scrapling_dynamic_enabled, scrapling_stealthy_enabled, updated_at)
                   VALUES ('default', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     fetch_chain = excluded.fetch_chain,
                     search_provider = excluded.search_provider,
                     fallback_mode = excluded.fallback_mode,
                     scrapling_dynamic_enabled = excluded.scrapling_dynamic_enabled,
                     scrapling_stealthy_enabled = excluded.scrapling_stealthy_enabled,
                     updated_at = excluded.updated_at""",
                (
                    json.dumps(fetch_chain),
                    search_provider,
                    fallback_mode,
                    1 if scrapling_dynamic_enabled else 0,
                    1 if scrapling_stealthy_enabled else 0,
                    now,
                ),
            )
