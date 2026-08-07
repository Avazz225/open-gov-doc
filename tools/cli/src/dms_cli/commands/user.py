"""Nutzerverwaltung (4.4) - Wrapper um auth-service `/users`, serverseitig
weiterhin auf `admin.user_management` gegated (P6-S5)."""

from __future__ import annotations

import getpass

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

user_app = typer.Typer(help="Nutzerverwaltung (auth-service, 4.4).")


def register(app: typer.Typer) -> None:
    app.add_typer(user_app, name="user")


@user_app.command("list")
def list_cmd(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("auth-service", "users")
    emit(result, output_format=output_format(ctx), columns=["id", "username", "email", "enabled"])


@user_app.command("create")
def create_cmd(
    ctx: typer.Context,
    username: str = typer.Option(..., "--username"),
    email: str = typer.Option(..., "--email"),
    first_name: str = typer.Option(..., "--first-name"),
    last_name: str = typer.Option(..., "--last-name"),
    password: str | None = typer.Option(
        None, "--password", help="Ohne Angabe wird interaktiv nach dem Initialpasswort gefragt."
    ),
) -> None:
    if password is None:
        password = getpass.getpass("Initialpasswort: ")
    client = get_client()
    result = client.post(
        "auth-service",
        "users",
        json_body={
            "username": username,
            "email": email,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        },
    )
    emit(result, output_format=output_format(ctx))


@user_app.command("delete")
def delete_cmd(ctx: typer.Context, user_id: str) -> None:
    client = get_client()
    client.delete("auth-service", f"users/{user_id}")
    typer.echo(f"Nutzer {user_id} geloescht.")
