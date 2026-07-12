"""Tianshu CLI entry point."""

from __future__ import annotations

import typer

from tianshu.cli.commands import (
    auth,
    config,
    cost,
    dag,
    decree,
    doctor,
    edict,
    evals,
    event,
    health,
    keqing,
    memorial,
    plugin,
    provider,
    schedule,
    secrets,
    worker,
    workspace,
)
from tianshu.cli.commands.watch import watch

app = typer.Typer(name="tianshu", help="Tianshu - AI Execution Platform CLI")

app.add_typer(auth.app, name="auth", help="Manage CLI authentication")
app.add_typer(edict.app, name="edict", help="Manage edicts")
app.add_typer(memorial.app, name="memorial", help="View memorials")
app.add_typer(config.app, name="config", help="Manage LLM configuration")
app.add_typer(decree.app, name="decree", help="Manage decrees (approvals)")
app.add_typer(event.app, name="event", help="Query events")
app.add_typer(schedule.app, name="schedule", help="Manage scheduled jobs")
app.add_typer(cost.app, name="cost", help="Cost management")
app.add_typer(provider.app, name="provider", help="Provider management")
app.add_typer(plugin.app, name="plugin", help="Plugin management")
app.add_typer(dag.app, name="dag", help="DAG execution management")
app.add_typer(worker.app, name="worker", help="Worker pool management")
app.add_typer(evals.app, name="evals", help="Platform regression evals & failure attribution")
app.add_typer(secrets.app, name="secrets", help="Credential master-key management")
app.add_typer(keqing.app, name="keqing", help="Keqing external executors (Claude Code / Codex)")
app.add_typer(workspace.app, name="workspace", help="Governed workspace status and apply")
app.add_typer(keqing.shadow_app, name="shadow", help="Shadow snapshots (one-click rollback)")
app.command()(health.health)
app.command()(doctor.doctor)
app.command()(watch)


if __name__ == "__main__":
    app()
