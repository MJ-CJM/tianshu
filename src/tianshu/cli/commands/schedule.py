"""Schedule management commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import api_delete, api_get

app = typer.Typer()
console = Console()


@app.command("list")
def list_jobs(
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """List scheduled jobs."""
    data = api_get("/api/scheduler/jobs")

    jobs = data.get("data", [])

    if fmt == "json":
        console.print_json(json.dumps(jobs))
        return

    table = Table(title=f"Scheduled Jobs ({len(jobs)})")
    table.add_column("Job ID", style="dim", max_width=26)
    table.add_column("Edict ID", style="dim", max_width=26)
    table.add_column("类型", max_width=12)
    table.add_column("下次运行", max_width=25)

    for j in jobs:
        table.add_row(
            j.get("job_id", ""),
            j.get("edict_id", ""),
            j.get("schedule_type", ""),
            j.get("next_run") or "—",
        )

    console.print(table)


@app.command()
def cancel(
    job_id: str = typer.Argument(help="Job ID to cancel"),
):
    """Cancel a scheduled job."""
    api_delete(f"/api/scheduler/jobs/{job_id}")
    console.print(f"[green]Job {job_id} cancelled.[/green]")
