"""tianshu secrets — 凭证主密钥管理(迭代 3「深防御」D16)。

凭证已 Fernet 密文落库(TIANSHU_SECRET_MASTER_KEY)。本命令补主密钥轮换:
用旧密钥全量解密 → 新密钥重新加密回写。轮换是危险操作(密钥错则全部凭证
不可解),故:①先干跑校验所有密文能用旧密钥解开;②备份 DB;③再回写。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import typer
from rich.console import Console

app = typer.Typer()
console = Console()


def _new_rotation_backup_path(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return db_path.with_name(f"{db_path.name}.rotation-{timestamp}-{uuid4().hex}.bak")


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
    """轮换凭证主密钥:旧密钥解密 → 新密钥重加密回写。

    轮换后须把 TIANSHU_SECRET_MASTER_KEY 更新为新密钥并重启,否则启动即
    无法解密任何凭证。
    """
    import os

    from cryptography.fernet import Fernet, InvalidToken

    from tianshu.config import TianshuSettings
    from tianshu.storage import Storage

    old_key = old_key or os.environ.get("TIANSHU_SECRET_MASTER_KEY")
    if not old_key:
        console.print("[red]✗ 缺旧密钥:--old-key 或设 TIANSHU_SECRET_MASTER_KEY[/red]")
        raise typer.Exit(1)
    try:
        old_fernet = Fernet(old_key.encode())
        new_fernet = Fernet(new_key.encode())
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]✗ 密钥格式非法(须 Fernet.generate_key() 输出):{e}[/red]")
        raise typer.Exit(1) from None

    settings = TianshuSettings()
    db_path = Path(settings.db_path).expanduser()
    storage = Storage(str(db_path))
    storage.init_db()
    try:
        creds = storage.list_credentials()
        if not creds:
            console.print("[yellow]库中无凭证,无需轮换[/yellow]")
            return

        # ① 干跑:全部密文必须能用旧密钥解开(有一条解不开就中止,避免半途)
        decrypted: dict[str, str] = {}
        for row in creds:
            try:
                decrypted[row["id"]] = old_fernet.decrypt(row["encrypted_value"]).decode("utf-8")
            except InvalidToken:
                console.print(
                    f"[red]✗ 凭证 {row['id']} 无法用旧密钥解密——轮换中止(未改动任何数据)[/red]"
                )
                raise typer.Exit(1) from None
        console.print(f"[green]✓[/green] 干跑通过:{len(decrypted)} 条凭证均可用旧密钥解密")

        if not yes:
            confirm = typer.confirm(f"将用新密钥重加密 {len(decrypted)} 条凭证并回写,继续?")
            if not confirm:
                raise typer.Exit(0)

        # ② 通过当前连接在线备份，确保 WAL 中已提交数据也进入备份。
        from tianshu.storage.sqlite_backup import create_online_backup

        backup = _new_rotation_backup_path(db_path)
        create_online_backup(storage._conn, backup)
        console.print(f"[dim]已备份数据库 → {backup}[/dim]")

        # ③ 新密钥重加密回写
        now_iso = datetime.now(UTC).isoformat()
        for cred_id, plaintext in decrypted.items():
            storage.update_credential(
                cred_id,
                encrypted_value=new_fernet.encrypt(plaintext.encode("utf-8")),
                now_iso=now_iso,
            )
        console.print(
            f"[green]✓[/green] 轮换完成:{len(decrypted)} 条凭证已用新密钥重加密。\n"
            f"[bold yellow]下一步:把 TIANSHU_SECRET_MASTER_KEY 更新为新密钥并重启天枢。[/bold yellow]"
        )
    finally:
        storage.close()
