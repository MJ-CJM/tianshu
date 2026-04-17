"""CLI commands for DAG operations."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.tree import Tree

from tianshu.cli.client import api_get, api_post

app = typer.Typer()
console = Console()


@app.command("show")
def dag_show(edict_id: str = typer.Argument(..., help="Edict ID")):
    """Show DAG execution for an edict (ASCII tree)."""
    resp = api_get(f"/api/dag/by-edict/{edict_id}")
    data = resp.get("data")
    if not data:
        console.print(f"[yellow]No DAG found for edict {edict_id}[/yellow]")
        raise typer.Exit()

    dag = data
    console.print(f"\n[bold]DAG Execution[/bold]: {dag['id']}")
    console.print(f"Status: {_status_badge(dag['status'])}")
    console.print(f"Created: {dag['created_at']}")
    if dag.get("completed_at"):
        console.print(f"Completed: {dag['completed_at']}")

    nodes = dag.get("nodes", [])
    if not nodes:
        console.print("[dim]No nodes[/dim]")
        return

    # Build dependency map for tree display
    roots = [n for n in nodes if not n.get("depends_on") or len(n["depends_on"]) == 0]
    children_map: dict[str, list[dict]] = {}
    for n in nodes:
        for dep in n.get("depends_on", []):
            children_map.setdefault(dep, []).append(n)

    tree = Tree("[bold]DAG Nodes[/bold]")
    visited: set[str] = set()

    def _add_node(parent, node_data: dict) -> None:
        nid = node_data["node_id"]
        if nid in visited:
            return
        visited.add(nid)
        status = _status_badge(node_data["status"])
        official = node_data.get("assigned_official") or ""
        label = f"{nid} {status} {node_data['description'][:60]}"
        if official:
            label += f" [{official}]"
        branch = parent.add(label)
        for child in children_map.get(nid, []):
            _add_node(branch, child)

    for root in roots:
        _add_node(tree, root)

    # Add any unvisited nodes (disconnected)
    for n in nodes:
        if n["node_id"] not in visited:
            status = _status_badge(n["status"])
            tree.add(f"{n['node_id']} {status} {n['description'][:60]}")

    console.print(tree)


@app.command("cancel")
def dag_cancel(dag_id: str = typer.Argument(..., help="DAG Execution ID")):
    """Cancel a running DAG execution."""
    resp = api_post(f"/api/dag/{dag_id}/cancel", {})
    if resp.get("success"):
        console.print(f"[green]DAG {dag_id} cancelled[/green]")
    else:
        console.print(f"[red]Failed: {resp.get('error')}[/red]")


@app.command("retry")
def dag_retry(
    dag_id: str = typer.Argument(..., help="DAG Execution ID"),
    nodes: str = typer.Option(None, "--nodes", "-n", help="Comma-separated node IDs to retry"),
):
    """Retry failed nodes in a DAG execution."""
    body: dict = {}
    if nodes:
        body["from_node_ids"] = [n.strip() for n in nodes.split(",")]
    resp = api_post(f"/api/dag/{dag_id}/retry", body)
    if resp.get("success"):
        reset = resp.get("data", {}).get("reset_node_ids", [])
        console.print(f"[green]Retry initiated. Reset nodes: {reset}[/green]")
    else:
        console.print(f"[red]Failed: {resp.get('error')}[/red]")


def _status_badge(status: str) -> str:
    colors = {
        "pending": "dim",
        "ready": "blue",
        "running": "yellow",
        "completed": "green",
        "failed": "red",
        "cancelled": "dim strikethrough",
    }
    color = colors.get(status, "white")
    return f"[{color}]{status}[/{color}]"
