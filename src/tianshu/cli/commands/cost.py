"""CLI commands for cost management."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import get_client

app = typer.Typer()
console = Console()


@app.command("summary")
def cost_summary(
    period: str = typer.Option("month", help="Period: day, week, month"),
    edict_id: str = typer.Option(None, help="Filter by edict ID"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show cost summary."""
    client = get_client()
    params = {"period": period}
    if edict_id:
        params["edict_id"] = edict_id
    resp = client.get("/api/cost/summary", params=params)
    resp.raise_for_status()
    data = resp.json()["data"]

    if as_json:
        console.print_json(json.dumps(data))
        return

    table = Table(title=f"Cost Summary ({period})")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total Records", str(data.get("total_records", 0)))
    table.add_row("Prompt Tokens", f"{data.get('total_prompt_tokens', 0):,}")
    table.add_row("Completion Tokens", f"{data.get('total_completion_tokens', 0):,}")
    table.add_row("Total Tokens", f"{data.get('total_tokens', 0):,}")
    table.add_row("Total Cost (CNY)", f"¥{data.get('total_cost_cny', 0):.4f}")
    console.print(table)


@app.command("budget")
def cost_budget(
    scope: str = typer.Option("global", help="Budget scope"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show budget status."""
    client = get_client()
    resp = client.get("/api/cost/budget", params={"scope": scope})
    resp.raise_for_status()
    data = resp.json()["data"]

    if as_json:
        console.print_json(json.dumps(data))
        return

    if not data:
        console.print("[yellow]No budget configured[/yellow]")
        return

    console.print(f"[bold]Scope:[/bold] {data['scope']}")
    console.print(f"[bold]Budget:[/bold] ${data['budget_cny']:.2f}")
    console.print(f"[bold]Spent:[/bold] ${data['spent_cny']:.2f}")
    console.print(f"[bold]Remaining:[/bold] ${data['remaining_cny']:.2f}")
    console.print(f"[bold]Period:[/bold] {data['period']}")
    if data.get("exceeded"):
        console.print("[red bold]BUDGET EXCEEDED[/red bold]")


@app.command("records")
def cost_records(
    edict_id: str = typer.Option(None, help="Filter by edict ID"),
    limit: int = typer.Option(20, help="Number of records"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List cost records."""
    client = get_client()
    params = {"limit": limit}
    if edict_id:
        params["edict_id"] = edict_id
    resp = client.get("/api/cost/records", params=params)
    resp.raise_for_status()
    data = resp.json()["data"]

    if as_json:
        console.print_json(json.dumps(data))
        return

    table = Table(title="Cost Records")
    table.add_column("Time", width=20)
    table.add_column("Edict", width=16)
    table.add_column("Model", width=14)
    table.add_column("Tokens", justify="right")
    table.add_column("Cost", justify="right")
    for r in data:
        table.add_row(
            r.get("created_at", "")[:19],
            r.get("edict_id", "")[:12] + "...",
            r.get("model", "-"),
            f"{r.get('total_tokens', 0):,}",
            f"¥{r.get('cost_cny', 0):.4f}",
        )
    console.print(table)
