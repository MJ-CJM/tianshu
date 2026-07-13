"""Health check command."""

from __future__ import annotations

import typer
from rich.console import Console

from tianshu.cli.client import api_get

console = Console()


def health():
    """Check Tianshu service readiness (/health/ready)."""
    try:
        data = api_get("/health/ready")
    except SystemExit:
        console.print("[red]FAIL[/red] Tianshu is not ready or unavailable", style="bold")
        raise typer.Exit(1) from None
    status = str(data.get("status", "unknown"))
    if status == "ready":
        console.print("[green]OK[/green] Tianshu is ready", style="bold")
    elif status == "degraded":
        console.print("[yellow]DEGRADED[/yellow] optional integrations unhealthy", style="bold")
    else:
        console.print(f"[red]FAIL[/red] readiness status: {status}", style="bold")
        raise typer.Exit(1)
