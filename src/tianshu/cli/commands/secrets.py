"""tianshu secrets — 全密文族主密钥管理。

network、channel 与 MCP secret 均以 Fernet 密文落库。本命令先全量干跑校验，
再在线备份，最后以单事务轮换所有非空密文并写入系统审计。
"""

from __future__ import annotations

import base64
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import typer
from rich.console import Console

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

    from tianshu.models.system_audit import AppendSystemAuditRequest

app = typer.Typer()
console = Console()


@dataclass(frozen=True)
class _RotationTarget:
    table: str
    rowid: int
    ciphertext_column: str
    original_ciphertext: bytes
    plaintext: bytes


_ROTATION_FAMILIES = (
    ("network_credentials", "encrypted_value"),
    ("channel_configs", "encrypted_secret"),
    ("channel_instances", "encrypted_secret"),
    ("mcp_server_overrides", "env_ciphertext"),
    ("mcp_server_overrides", "headers_ciphertext"),
)


class _SecretRotationConcurrentChange(RuntimeError):
    pass


type _RotationSnapshot = tuple[tuple[str, str, int, bytes], ...]


def _new_rotation_backup_path(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return db_path.with_name(f"{db_path.name}.rotation-{timestamp}-{uuid4().hex}.bak")


def _rotation_snapshot(conn: sqlite3.Connection) -> _RotationSnapshot:
    snapshot: list[tuple[str, str, int, bytes]] = []
    for table, ciphertext_column in _ROTATION_FAMILIES:
        rows = conn.execute(
            f"SELECT rowid, {ciphertext_column} FROM {table} "
            f"WHERE {ciphertext_column} IS NOT NULL ORDER BY rowid"
        ).fetchall()
        snapshot.extend((table, ciphertext_column, int(row[0]), row[1]) for row in rows)
    return tuple(snapshot)


def _plan_snapshot(plan: list[_RotationTarget]) -> _RotationSnapshot:
    return tuple(
        (target.table, target.ciphertext_column, target.rowid, target.original_ciphertext)
        for target in plan
    )


def _rotation_plan(conn: sqlite3.Connection, old_fernet: Fernet) -> list[_RotationTarget]:
    from cryptography.fernet import InvalidToken

    plan: list[_RotationTarget] = []
    try:
        for table, ciphertext_column, rowid, original_ciphertext in _rotation_snapshot(conn):
            plaintext = old_fernet.decrypt(original_ciphertext)
            decoded = plaintext.decode("utf-8")
            if table == "mcp_server_overrides":
                mapping = json.loads(decoded)
                if not isinstance(mapping, dict) or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in mapping.items()
                ):
                    raise ValueError("invalid MCP mapping")
            plan.append(
                _RotationTarget(
                    table=table,
                    rowid=rowid,
                    ciphertext_column=ciphertext_column,
                    original_ciphertext=original_ciphertext,
                    plaintext=plaintext,
                )
            )
    except (InvalidToken, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("secret rotation validation failed") from None
    return plan


def _rotation_audit_request() -> AppendSystemAuditRequest:
    import hashlib

    from tianshu.models.system_audit import AppendSystemAuditRequest

    return AppendSystemAuditRequest(
        correlation_id=f"secrets-rotation-{uuid4().hex}",
        actor_digest=hashlib.sha256(b"tianshu-secrets-cli").hexdigest(),
        action="secrets.master_key.rotated",
        outcome="succeeded",
        reason_code="master_key_rotated",
        subject_kind="secret_master_key",
        subject_digest=hashlib.sha256(b"tianshu-secret-master-key").hexdigest(),
        metadata={},
    )


@app.command("gen-key")
def gen_key():
    """生成一个新的 Fernet 主密钥(填入 TIANSHU_SECRET_MASTER_KEY)。"""
    from cryptography.fernet import Fernet

    console.print(Fernet.generate_key().decode())


@app.command("rotate-master-key")
def rotate_master_key(
    new_key: str = typer.Option(..., "--new-key", help="新主密钥(Fernet.generate_key() 输出)"),
    old_key: str | None = typer.Option(
        None, "--old-key", help="旧主密钥(默认读 TIANSHU_SECRET_MASTER_KEY)"
    ),
    yes: bool = typer.Option(False, "--yes", help="跳过确认(脚本化)"),
):
    """原子轮换所有密文族的主密钥。

    轮换后须把 TIANSHU_SECRET_MASTER_KEY 更新为新密钥并重启,否则启动即
    无法解密任何 secret。
    """
    import os

    from cryptography.fernet import Fernet

    from tianshu.config import TianshuSettings
    from tianshu.storage import Storage

    old_key = old_key or os.environ.get("TIANSHU_SECRET_MASTER_KEY")
    if not old_key:
        console.print("secret_rotation_missing_old_key", style="red")
        raise typer.Exit(1)
    try:
        old_fernet = Fernet(old_key.encode())
        new_fernet = Fernet(new_key.encode())
        old_key_material = base64.urlsafe_b64decode(old_key.encode())
        new_key_material = base64.urlsafe_b64decode(new_key.encode())
    except Exception:  # noqa: BLE001
        console.print("secret_rotation_invalid_key", style="red")
        raise typer.Exit(1) from None
    if hmac.compare_digest(old_key_material, new_key_material):
        console.print("secret_rotation_same_key", style="red")
        raise typer.Exit(1)

    settings = TianshuSettings()
    db_path = Path(settings.db_path).expanduser()
    storage = Storage(str(db_path))
    storage.init_db()
    try:
        try:
            plan = _rotation_plan(storage._conn, old_fernet)
        except ValueError:
            console.print("secret_rotation_validation_failed", style="red")
            raise typer.Exit(1) from None
        if not plan:
            console.print("secret_rotation_noop: 0 条密文,无需轮换", style="yellow")
            return

        console.print(f"secret_rotation_validated: {len(plan)} 条密文", style="green")

        if not yes:
            confirm = typer.confirm(f"将用新密钥重加密 {len(plan)} 条密文并回写,继续?")
            if not confirm:
                raise typer.Exit(0)

        from tianshu.storage.sqlite_backup import create_online_backup

        backup = _new_rotation_backup_path(db_path)
        try:
            create_online_backup(storage._conn, backup)
        except Exception:  # noqa: BLE001
            console.print("secret_rotation_backup_failed", style="red")
            raise typer.Exit(1) from None
        console.print("secret_rotation_backup_created")

        now_iso = datetime.now(UTC).isoformat()
        try:
            from tianshu.storage import system_audit_repo

            with storage._lock, storage._conn:
                storage._conn.execute("BEGIN IMMEDIATE")
                if _rotation_snapshot(storage._conn) != _plan_snapshot(plan):
                    raise _SecretRotationConcurrentChange
                for target in plan:
                    cursor = storage._conn.execute(
                        f"UPDATE {target.table} "
                        f"SET {target.ciphertext_column} = ?, updated_at = ? "
                        f"WHERE rowid = ? AND {target.ciphertext_column} = ?",
                        (
                            new_fernet.encrypt(target.plaintext),
                            now_iso,
                            target.rowid,
                            target.original_ciphertext,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise _SecretRotationConcurrentChange
                system_audit_repo._append_system_audit_unlocked(
                    storage._conn,
                    _rotation_audit_request(),
                )
        except _SecretRotationConcurrentChange:
            console.print("secret_rotation_concurrent_change", style="red")
            raise typer.Exit(1) from None
        except Exception:  # noqa: BLE001
            console.print("secret_rotation_commit_failed", style="red")
            raise typer.Exit(1) from None
        console.print(f"secret_rotation_succeeded: {len(plan)} 条密文", style="green")
    finally:
        storage.close()
