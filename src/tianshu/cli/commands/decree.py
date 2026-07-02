"""Decree (approval) management commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console

from tianshu.cli.client import api_get, api_post

app = typer.Typer()
console = Console()


@app.command()
def submit(
    memorial_id: str = typer.Option(..., "--memorial-id", "-m", help="Memorial ID"),
    action: str = typer.Option(..., "--action", "-a", help="Action: approve|reject|retry|amend|cancel"),
    comment: str = typer.Option(None, "--comment", help="Optional comment"),
    amended_goal: str = typer.Option(None, "--amended-goal", help="New goal (required for amend)"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """Submit a decree (approval decision)."""
    body: dict = {
        "memorial_id": memorial_id,
        "action": action,
        "actor": "cli",
    }
    if comment:
        body["comment"] = comment
    if amended_goal:
        body["amended_goal"] = amended_goal

    data = api_post("/api/decrees", body)

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    decree = data.get("data", {})
    console.print("\n[bold]Decree submitted[/bold]")
    console.print(f"  ID:          {decree.get('id', 'N/A')}")
    console.print(f"  Memorial ID: {decree.get('memorial_id', 'N/A')}")
    console.print(f"  Action:      {decree.get('action', 'N/A')}")
    if decree.get("comment"):
        console.print(f"  Comment:     {decree['comment']}")
    if decree.get("amended_goal"):
        console.print(f"  New goal:    {decree['amended_goal']}")


@app.command("list")
def list_decrees(
    memorial_id: str = typer.Option(..., "--memorial-id", "-m", help="Memorial ID"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """List decrees for a memorial."""
    data = api_get(f"/api/memorials/{memorial_id}")

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    m = data.get("data", {})
    console.print(f"\n[bold]Memorial {memorial_id}[/bold]")
    console.print(f"  Status:        {m.get('status', 'N/A')}")
    console.print(f"  Review status: {m.get('review_status', 'N/A')}")
    if m.get("audit"):
        audit = m["audit"]
        console.print(f"  Audit verdict: {audit.get('verdict', 'N/A')}")
        for reason in audit.get("reasons", []):
            console.print(f"    - {reason}")
