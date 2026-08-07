"""Lokale CLI-Konfiguration anzeigen/aendern (Gateway-URL)."""

from __future__ import annotations

import typer

from dms_cli import credentials
from dms_cli.context import output_format
from dms_cli.output import emit

config_app = typer.Typer(help="Gespeicherte Gateway-URL/Zugangsdaten anzeigen oder aendern.")


def register(app: typer.Typer) -> None:
    app.add_typer(config_app, name="config")


@config_app.command("show")
def show(ctx: typer.Context) -> None:
    creds = credentials.load_credentials()
    if creds is None:
        typer.echo("Keine gespeicherten Zugangsdaten - 'dms login' ausfuehren.", err=True)
        raise typer.Exit(code=1)
    data = {
        "gateway_url": creds.gateway_url,
        "username": creds.username,
        "angemeldet": bool(creds.access_token),
    }
    emit(data, output_format=output_format(ctx))


@config_app.command("set-gateway-url")
def set_gateway_url(url: str) -> None:
    creds = credentials.load_credentials() or credentials.Credentials(gateway_url=url)
    creds.gateway_url = url
    credentials.save_credentials(creds)
    typer.echo(f"Gateway-URL gesetzt: {url}")
