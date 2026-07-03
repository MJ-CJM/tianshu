"""Storage Channel 领域 Mixin —— 通道配置（飞书/Telegram）与多 Bot 实例管理。"""

import sqlite3
import threading


class ChannelMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Channel configs (通政司) ---

    def get_channel_config(self, channel_type: str) -> dict | None:
        """返回非敏感配置 dict + secret 是否已配（不返明文）。None 表示未配置。"""
        row = self._conn.execute(
            "SELECT config_json, encrypted_secret, updated_at "
            "FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()
        if not row:
            return None
        import json as _json

        cfg = _json.loads(row[0])
        cfg["_has_secret"] = row[1] is not None
        cfg["_updated_at"] = row[2]
        return cfg

    def save_channel_config(
        self,
        channel_type: str,
        config: dict,
        secret_plaintext: str | None = None,
    ) -> None:
        """保存配置；secret_plaintext=None 时不动 encrypted_secret，空串清空。"""
        import json as _json
        from datetime import UTC, datetime

        from tianshu.secrets.vault import get_vault

        # 排除内部字段
        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        config_json_str = _json.dumps(clean_config, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        encrypted: bytes | None = None
        update_secret = False
        if secret_plaintext is not None:
            update_secret = True
            if secret_plaintext == "":
                encrypted = None  # 清空
            else:
                vault = get_vault()
                if vault is None:
                    raise RuntimeError(
                        "TIANSHU_SECRET_MASTER_KEY 未设置，无法保存敏感凭证。"
                        "请先配置主密钥后重启服务。"
                    )
                encrypted = vault.encrypt(secret_plaintext)

        existing = self._conn.execute(
            "SELECT 1 FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()

        if existing:
            if update_secret:
                self._conn.execute(
                    "UPDATE channel_configs SET config_json=?, encrypted_secret=?, updated_at=? "
                    "WHERE channel_type=?",
                    (config_json_str, encrypted, now, channel_type),
                )
            else:
                self._conn.execute(
                    "UPDATE channel_configs SET config_json=?, updated_at=? WHERE channel_type=?",
                    (config_json_str, now, channel_type),
                )
        else:
            self._conn.execute(
                "INSERT INTO channel_configs (channel_type, config_json, encrypted_secret, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (channel_type, config_json_str, encrypted, now),
            )
        self._conn.commit()

    def load_channel_runtime_config(self, channel_type: str) -> dict | None:
        """返回**含明文 secret** 的运行时配置；启动加载/reload 用，不暴露给 API。"""
        row = self._conn.execute(
            "SELECT config_json, encrypted_secret FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()
        if not row:
            return None
        import json as _json

        from tianshu.secrets.vault import get_vault

        cfg = _json.loads(row[0])
        # 解密后的 secret 放到 channel 对应的字段：飞书=app_secret，telegram=bot_token
        secret_key = "bot_token" if channel_type == "telegram" else "app_secret"
        if row[1]:
            vault = get_vault()
            if vault is None:
                return None  # 配了 secret 但 vault 缺失 → 视为不可用
            try:
                cfg[secret_key] = vault.decrypt(row[1])
            except ValueError:
                return None
        else:
            cfg[secret_key] = ""
        return cfg

    # --- Channel instances（多 bot 实例）---

    def list_channel_instances(self, channel_type: str | None = None) -> list[dict]:
        """列实例（不含明文 secret）。每行展开 config + instance_id / channel_type /
        label / enabled(bool) / _has_secret / updated_at。"""
        import json as _json

        sql = (
            "SELECT instance_id, channel_type, label, enabled, config_json, "
            "encrypted_secret, updated_at FROM channel_instances"
        )
        params: tuple = ()
        if channel_type is not None:
            sql += " WHERE channel_type = ?"
            params = (channel_type,)
        sql += " ORDER BY channel_type, instance_id"
        rows = self._conn.execute(sql, params).fetchall()
        result: list[dict] = []
        for row in rows:
            cfg = _json.loads(row[4])
            cfg["instance_id"] = row[0]
            cfg["channel_type"] = row[1]
            cfg["label"] = row[2]
            cfg["enabled"] = bool(row[3])
            cfg["_has_secret"] = row[5] is not None
            cfg["updated_at"] = row[6]
            result.append(cfg)
        return result

    def get_channel_instance(self, instance_id: str) -> dict | None:
        """单条实例（不含明文 secret）。None 表示不存在。"""
        import json as _json

        row = self._conn.execute(
            "SELECT instance_id, channel_type, label, enabled, config_json, "
            "encrypted_secret, updated_at FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if not row:
            return None
        cfg = _json.loads(row[4])
        cfg["instance_id"] = row[0]
        cfg["channel_type"] = row[1]
        cfg["label"] = row[2]
        cfg["enabled"] = bool(row[3])
        cfg["_has_secret"] = row[5] is not None
        cfg["updated_at"] = row[6]
        return cfg

    def save_channel_instance(
        self,
        *,
        instance_id: str,
        channel_type: str,
        label: str,
        enabled: bool,
        config: dict,
        secret_plaintext: str | None = None,
    ) -> None:
        """保存实例。secret_plaintext=None 时不动 encrypted_secret，空串清空，
        非空则 vault.encrypt（vault 缺失 raise RuntimeError）。config 去掉 _ 开头的 key。"""
        import json as _json
        from datetime import UTC, datetime

        from tianshu.secrets.vault import get_vault

        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        config_json_str = _json.dumps(clean_config, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        encrypted: bytes | None = None
        update_secret = False
        if secret_plaintext is not None:
            update_secret = True
            if secret_plaintext == "":
                encrypted = None  # 清空
            else:
                vault = get_vault()
                if vault is None:
                    raise RuntimeError(
                        "TIANSHU_SECRET_MASTER_KEY 未设置，无法保存敏感凭证。"
                        "请先配置主密钥后重启服务。"
                    )
                encrypted = vault.encrypt(secret_plaintext)

        existing = self._conn.execute(
            "SELECT 1 FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()

        if existing:
            if update_secret:
                self._conn.execute(
                    "UPDATE channel_instances SET channel_type=?, label=?, enabled=?, "
                    "config_json=?, encrypted_secret=?, updated_at=? WHERE instance_id=?",
                    (
                        channel_type,
                        label,
                        int(enabled),
                        config_json_str,
                        encrypted,
                        now,
                        instance_id,
                    ),
                )
            else:
                self._conn.execute(
                    "UPDATE channel_instances SET channel_type=?, label=?, enabled=?, "
                    "config_json=?, updated_at=? WHERE instance_id=?",
                    (channel_type, label, int(enabled), config_json_str, now, instance_id),
                )
        else:
            self._conn.execute(
                "INSERT INTO channel_instances "
                "(instance_id, channel_type, label, enabled, config_json, "
                "encrypted_secret, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (instance_id, channel_type, label, int(enabled), config_json_str, encrypted, now),
            )
        self._conn.commit()

    def set_channel_instance_enabled(self, instance_id: str, enabled: bool) -> None:
        from datetime import UTC, datetime

        self._conn.execute(
            "UPDATE channel_instances SET enabled=?, updated_at=? WHERE instance_id=?",
            (int(enabled), datetime.now(UTC).isoformat(), instance_id),
        )
        self._conn.commit()

    def delete_channel_instance(self, instance_id: str) -> None:
        self._conn.execute(
            "DELETE FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        )
        self._conn.commit()

    def load_channel_instance_runtime(self, instance_id: str) -> dict | None:
        """返回**含明文 secret** 的运行时配置（启动/reload 用）。

        返回 dict 展开 config + instance_id / channel_type / label / enabled，
        且 secret 解密放到 bot_token(telegram) / app_secret(feishu)。
        vault 缺失或解密失败时：若该实例确实存了 secret 则返回 None（视为不可用）；
        无 secret 则 secret 字段填空串。
        """
        import json as _json

        from tianshu.secrets.vault import get_vault

        row = self._conn.execute(
            "SELECT channel_type, label, enabled, config_json, encrypted_secret "
            "FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if not row:
            return None
        channel_type = row[0]
        cfg = _json.loads(row[3])
        cfg["instance_id"] = instance_id
        cfg["channel_type"] = channel_type
        cfg["label"] = row[1]
        cfg["enabled"] = bool(row[2])
        secret_key = "bot_token" if channel_type == "telegram" else "app_secret"
        if row[4]:
            vault = get_vault()
            if vault is None:
                return None  # 配了 secret 但 vault 缺失 → 视为不可用
            try:
                cfg[secret_key] = vault.decrypt(row[4])
            except ValueError:
                return None
        else:
            cfg[secret_key] = ""
        return cfg
