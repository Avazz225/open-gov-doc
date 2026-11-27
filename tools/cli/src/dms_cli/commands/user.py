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


@user_app.command("reset-password")
def reset_password_cmd(
    user_id: str = typer.Argument(..., help="Keycloak-ID des Kontos (dms user list)."),
    password: str | None = typer.Option(
        None, "--password", help="Ohne Angabe wird interaktiv gefragt (zweimal)."
    ),
    permanent: bool = typer.Option(
        False,
        "--permanent",
        help="Nicht temporär: kein Zwang, es beim nächsten Login zu ändern.",
    ),
) -> None:
    """Passwort eines anderen Kontos zurücksetzen (P98-S3).

    Standard: temporär - das Konto muss beim nächsten Login ein eigenes setzen.
    Abgelehnt, wenn das Zielkonto Rechte hat, die Sie nicht haben."""
    if password is None:
        password = getpass.getpass("Neues Passwort: ")
        if getpass.getpass("Wiederholen: ") != password:
            typer.echo("Die Eingaben stimmen nicht überein.", err=True)
            raise typer.Exit(code=1)
    get_client().post(
        "auth-service",
        f"users/{user_id}/password",
        json_body={"password": password, "temporary": not permanent},
    )
    if permanent:
        typer.echo(f"Passwort für {user_id} gesetzt.")
    else:
        typer.echo(
            f"Temporäres Passwort für {user_id} gesetzt - beim nächsten Login muss ein eigenes "
            "gewählt werden."
        )
