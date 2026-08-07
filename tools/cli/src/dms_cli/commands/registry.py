"""Registry-Status (3.2) - Einsicht in registrierte Service-Instanzen.
Plugin-Orchestrierung (3.8) existiert noch nicht (Phase 10), daher keine
entsprechenden Befehle - siehe docs/tools/cli.md "Offene Punkte"."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

registry_app = typer.Typer(help="Registry-Status (3.2).")


def register(app: typer.Typer) -> None:
    app.add_typer(registry_app, name="registry")


@registry_app.command("status")
def status_cmd(
    ctx: typer.Context,
    service_type: str | None = typer.Argument(
        None, help="Nur Instanzen dieses Diensttyps, sonst alle."
    ),
) -> None:
    client = get_client()
    path = f"instances/{service_type}" if service_type else "instances"
    result = client.get("registry-service", path)
    emit(
        result,
        output_format=output_format(ctx),
        columns=[
            "service_type",
            "instance_id",
            "version",
            "address",
            "healthy",
            "last_heartbeat_at",
        ],
    )
