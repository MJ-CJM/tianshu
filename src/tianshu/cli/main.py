"""Tianshu CLI entry point."""

from __future__ import annotations

import typer

from tianshu.cli.commands import config, edict, health, memorial

app = typer.Typer(name="tianshu", help="Tianshu - AI Execution Platform CLI")

app.add_typer(edict.app, name="edict", help="Manage edicts")
app.add_typer(memorial.app, name="memorial", help="View memorials")
app.add_typer(config.app, name="config", help="Manage LLM configuration")
app.command()(health.health)


if __name__ == "__main__":
    app()
