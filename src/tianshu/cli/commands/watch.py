"""Real-time edict watch command."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from tianshu.cli.client import auth_headers

console = Console()

_TERMINAL_TYPES = {"execution.completed", "execution.failed", "execution.cancelled"}


def _make_status_table(edict_id: str, events: list[dict], connected: bool) -> Table:
    table = Table(title=f"Watch: {edict_id}", expand=True)
    table.add_column("字段", style="bold", width=12)
    table.add_column("值")

    status = "等待中"
    event_count = len(events)
    start_time = None

    for ev in events:
        t = ev.get("type", "")
        if t == "execution.started":
            status = "[blue]执行中[/blue]"
            start_time = ev.get("created_at")
        elif t == "execution.completed":
            status = "[green]已完成[/green]"
        elif t == "execution.failed":
            status = "[red]已失败[/red]"
        elif t == "execution.cancelled":
            status = "[yellow]已取消[/yellow]"

    elapsed = "—"
    if start_time:
        try:
            started = datetime.fromisoformat(start_time)
            delta = datetime.now(UTC) - started
            elapsed = f"{delta.total_seconds():.1f}s"
        except (ValueError, TypeError):
            pass

    conn_status = "[green]已连接[/green]" if connected else "[red]已断开[/red]"
    table.add_row("连接状态", conn_status)
    table.add_row("任务状态", status)
    table.add_row("事件数", str(event_count))
    table.add_row("已用时间", elapsed)

    return table


def watch(
    edict_id: str = typer.Argument(help="Edict ID to watch"),
):
    """Watch edict execution in real-time via WebSocket."""
    try:
        import websockets.sync.client as ws_client
    except ImportError:
        console.print("[red]websockets 未安装，请执行: pip install 'tianshu-agent-os[cli]'[/red]")
        raise SystemExit(1) from None

    base = os.environ.get("TIANSHU_API_URL", "http://localhost:8000")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/api/ws"

    events: list[dict] = []
    done = False

    console.print(f"[bold]Connecting to {ws_url}...[/bold]")

    try:
        with ws_client.connect(ws_url, additional_headers=auth_headers()) as ws:
            console.print("[green]Connected. Watching...[/green]  (Ctrl+C to quit)\n")

            with Live(
                _make_status_table(edict_id, events, True), console=console, refresh_per_second=2
            ) as live:
                while not done:
                    try:
                        raw = ws.recv(timeout=1.0)
                    except TimeoutError:
                        live.update(_make_status_table(edict_id, events, True))
                        continue

                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    msg_edict = msg.get("edict_id")
                    if msg_edict and msg_edict != edict_id:
                        continue

                    events.append(msg)
                    event_type = msg.get("type", "unknown")

                    live.update(_make_status_table(edict_id, events, True))
                    console.print(
                        f"  [{_event_color(event_type)}]{event_type}[/{_event_color(event_type)}]"
                        f"  {_event_summary(msg)}"
                    )

                    if event_type in _TERMINAL_TYPES:
                        done = True

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
    except Exception as e:
        console.print(f"[red]Connection error: {e}[/red]", file=sys.stderr)
        raise SystemExit(1) from e

    console.print(f"\n[bold]Summary:[/bold] {len(events)} events received")
    if events:
        last = events[-1]
        console.print(f"  Final event: {last.get('type', 'unknown')}")


def _event_color(event_type: str) -> str:
    if "completed" in event_type:
        return "green"
    if "failed" in event_type:
        return "red"
    if "started" in event_type:
        return "blue"
    if "cancelled" in event_type:
        return "yellow"
    return "white"


def _event_summary(msg: dict) -> str:
    parts = []
    for key in ("status", "tool", "iteration", "goal", "verdict"):
        if key in msg:
            parts.append(f"{key}={msg[key]}")
    return " ".join(parts) if parts else ""
