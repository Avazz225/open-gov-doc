"""DMS-CLI (Konzept 6.2) - Root-Typer-App. Ein Client gegen das API-Gateway,
konzeptionell an `oc` (OpenShift) orientiert: dieselben Rechte/Sicherungsstufen
wie die Web-UIs, da jeder Aufruf ueber dasselbe Gateway/dieselbe RBAC-Pruefung
laeuft (siehe client.py)."""

from __future__ import annotations

import typer

from dms_cli.client import ApiError, AuthRequiredError
from dms_cli.commands import (
    archival,
    auth,
    config,
    object_type,
    query,
    registry,
    role,
    user,
    workflow,
)

app = typer.Typer(
    help="DMS-Kommandozeilenwerkzeug (Konzept 6.2) - Client gegen das API-Gateway.",
    no_args_is_help=True,
)


@app.callback()
def main_callback(
    ctx: typer.Context,
    output: str = typer.Option(
        "table", "--output", "-o", help="Ausgabeformat: table (Default) oder json."
    ),
) -> None:
    if output not in ("table", "json"):
        typer.echo("--output muss 'table' oder 'json' sein.", err=True)
        raise typer.Exit(code=2)
    ctx.obj = {"output": output}


auth.register(app)
config.register(app)
query.register(app)
object_type.register(app)
user.register(app)
role.register(app)
registry.register(app)
workflow.register(app)
archival.register(app)


def run() -> None:
    try:
        app()
    except (ApiError, AuthRequiredError) as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
