"""tianshu keqing / shadow — 客卿执行器与影子快照(迭代 3.5「客卿」)。

- `tianshu keqing agents`:列可用外部执行器;
- `tianshu shadow list <edict_id>` / `revert <edict_id> <sha>`:放手四保险③,
  查看某 edict 的客卿工作区快照并一键回滚。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from tianshu.executor.keqing import list_adapters

app = typer.Typer()
shadow_app = typer.Typer()
console = Console()


def _storage():
    from tianshu.config import TianshuSettings
    from tianshu.storage import Storage

    settings = TianshuSettings()
    storage = Storage(settings.db_path)
    storage.init_db()
    return storage


@app.command("agents")
def keqing_agents():
    """列出可用客卿 backend(edict.runtime.executor 取值)。"""
    console.print("[bold]native[/bold]  (自研引擎,默认)")
    for name in list_adapters():
        console.print(f"[bold]keqing:{name}[/bold]  (外部 CLI 客卿)")


@shadow_app.command("list")
def shadow_list(edict_id: str = typer.Argument(..., help="敕令 ID")):
    """列出某 edict 的客卿工作区影子快照。"""
    storage = _storage()
    snaps = storage.list_shadow_snapshots(edict_id)
    if not snaps:
        console.print(f"[dim]edict {edict_id} 无影子快照(仅客卿执行会产生)[/dim]")
        return
    table = Table(title=f"影子快照 · edict {edict_id[:12]}")
    table.add_column("SHA")
    table.add_column("标签")
    table.add_column("工作区")
    table.add_column("时间")
    for s in snaps:
        table.add_row(s["sha"][:10], s["label"], s["work_tree"], s["created_at"][:19])
    console.print(table)


@shadow_app.command("revert")
def shadow_revert(
    edict_id: str = typer.Argument(..., help="敕令 ID"),
    sha: str = typer.Argument(..., help="要回滚到的快照 SHA(可用 shadow list 查)"),
):
    """把某 edict 的客卿工作区回滚到指定快照(放手四保险③)。"""
    from tianshu.executor.shadow_snapshot import ShadowSnapshot

    storage = _storage()
    work_tree = storage.get_shadow_work_tree(edict_id)
    if not work_tree:
        console.print(f"[red]✗ edict {edict_id} 无影子快照[/red]")
        raise typer.Exit(1)
    shadow = ShadowSnapshot(Path(work_tree), edict_id)
    if shadow.revert(sha):
        console.print(f"[green]✓[/green] 已回滚工作区 {work_tree} 到快照 {sha[:10]}")
    else:
        console.print("[red]✗ 回滚失败(见日志)[/red]")
        raise typer.Exit(1)
