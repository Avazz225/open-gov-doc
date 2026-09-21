"""Plugin-Orchestrierung (3.8, `plugin-orchestration-service`) - P67-S2:
previously not wired into the CLI at all despite the service existing since
Phase 10 - `registry.py`'s own docstring had already flagged this gap
before it existed, see docs/tools/cli.md "Offene Punkte"."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

plugin_app = typer.Typer(help="Plugin-Orchestrierung (3.8).")
placements_app = typer.Typer(help="Platzierungsentscheidungen.")
plugin_app.add_typer(placements_app, name="placements")


def register(app: typer.Typer) -> None:
    app.add_typer(plugin_app, name="plugin-orchestration")


@plugin_app.command("status")
def status_cmd(ctx: typer.Context) -> None:
    """Kombiniert registrierte Plugin-Manifeste und Cluster-Knoten - die
    beiden Bausteine, aus denen jede Platzierungsentscheidung hervorgeht."""
    client = get_client()
    plugins = client.get("plugin-orchestration-service", "plugins")
    nodes = client.get("plugin-orchestration-service", "nodes")
    if output_format(ctx) == "json":
        emit({"plugins": plugins, "nodes": nodes}, output_format="json")
        return
    typer.echo("Registrierte Plugins:")
    emit(
        plugins,
        output_format="table",
        columns=["plugin_type", "version", "scaling_type", "resource_cpu_cores", "resource_ram_mb"],
    )
    typer.echo("\nCluster-Knoten:")
    emit(
        nodes,
        output_format="table",
        columns=["node_id", "cpu_cores", "cpu_usage_percent", "total_ram_mb", "available_ram_mb"],
    )


@placements_app.command("list")
def placements_list(
    ctx: typer.Context, plugin_type: str | None = typer.Option(None, "--plugin-type")
) -> None:
    client = get_client()
    result = client.get(
        "plugin-orchestration-service", "placements", params={"plugin_type": plugin_type}
    )
    emit(
        result,
        output_format=output_format(ctx),
        columns=["id", "plugin_type", "node_id", "placement_allowed", "reason", "decided_at"],
    )
