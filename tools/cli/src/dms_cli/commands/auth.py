"""Anmeldung (4.4) - dasselbe Password-Grant-Login wie die Web-UIs, ueber
das Gateway. Flach unter der Root-App (`dms login`, nicht `dms auth login`),
analog zu `oc login`."""

from __future__ import annotations

import getpass
import os

import typer

from dms_cli import client as gateway_client
from dms_cli import credentials
from dms_cli.context import get_client, output_format
from dms_cli.output import emit


def register(app: typer.Typer) -> None:
    app.command("login")(login)
    app.command("logout")(logout)
    app.command("whoami")(whoami)


def login(
    username: str = typer.Option(..., "--username", "-u", prompt=True),
    password: str | None = typer.Option(
        None, "--password", help="Ohne Angabe wird interaktiv nach dem Passwort gefragt."
    ),
    gateway_url: str = typer.Option(
        credentials.DEFAULT_GATEWAY_URL, "--gateway-url", help="Adresse des API-Gateways."
    ),
) -> None:
    if os.environ.get("DMS_TOKEN"):
        typer.echo(
            "Hinweis: DMS_TOKEN ist gesetzt und ueberschreibt gespeicherte Zugangsdaten - "
            "'dms login' wirkt erst, wenn DMS_TOKEN wieder unset ist.",
            err=True,
        )
    if password is None:
        password = getpass.getpass("Passwort: ")
    tokens = gateway_client.login(gateway_url, username, password)
    creds = credentials.Credentials(
        gateway_url=gateway_url,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        username=username,
    )
    credentials.save_credentials(creds)
    typer.echo(f"Angemeldet als {username} gegen {gateway_url}.")


def logout() -> None:
    credentials.clear_credentials()
    typer.echo("Abgemeldet, gespeicherte Zugangsdaten entfernt.")


def whoami(ctx: typer.Context) -> None:
    client = get_client()
    me = client.get("auth-service", "me")
    emit(me, output_format=output_format(ctx))
