"""Health check command."""

from __future__ import annotations

import typer
from rich.console import Console

from tianshu.cli.client import api_get

console = Console()


def health():
    """Check Tianshu service health."""
    try:
        api_get("/health")
        console.print("[green]OK[/green] Tianshu is running", style="bold")
    except SystemExit:
        console.print("[red]FAIL[/red] Tianshu is not available", style="bold")
        raise typer.Exit(1) from None
