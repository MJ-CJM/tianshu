"""CLI commands for worker management."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import api_get

app = typer.Typer()
console = Console()


@app.command("list")
def worker_list():
    """List active workers."""
    resp = api_get("/api/workers")
    data = resp.get("data", {})
    active = data.get("active", [])

    table = Table(title="Active Workers")
    table.add_column("Work ID")
    table.add_column("Node ID")
    table.add_column("DAG Execution")

    if not active:
        console.print("[dim]No active workers[/dim]")
        return

    for w in active:
        parts = w.split(":", 1)
        dag_id = parts[0] if len(parts) > 1 else ""
        node_id = parts[1] if len(parts) > 1 else w
        table.add_row(w, node_id, dag_id)

    console.print(table)


@app.command("status")
def worker_status():
    """Show worker pool and lane status."""
    resp = api_get("/api/workers/status")
    data = resp.get("data", {})

    pool = data.get("pool", {})
    lanes = data.get("lanes", {})

    console.print("\n[bold]Worker Pool[/bold]")
    console.print(f"  Active: {pool.get('active_count', 0)} / {pool.get('max_concurrency', 0)}")
    console.print(f"  Completed: {pool.get('completed_count', 0)}")
    console.print(f"  Failed: {pool.get('failed_count', 0)}")

    if lanes:
        gl = lanes.get("global", {})
        console.print(f"\n[bold]Global Lane[/bold]")
        console.print(f"  Active: {gl.get('active', 0)} / {gl.get('max_concurrency', 0)}")

        sessions = lanes.get("sessions", {})
        if sessions:
            console.print(f"\n[bold]Session Lanes[/bold]")
            for eid, info in sessions.items():
                console.print(f"  {eid}: available={info.get('available', 0)} / max={info.get('max', 0)}")
