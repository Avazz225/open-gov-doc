"""Mailboxes (P96-S2) - mail-connector `/mailbox-config`. Passwords are
write-only: they are never shown, and an update without one keeps it."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

mailbox_app = typer.Typer(help="Postfaecher (mail-connector).")


def register(app: typer.Typer) -> None:
    app.add_typer(mailbox_app, name="mailbox")


def _read(file: Path) -> dict:
    return json.loads(file.read_text(encoding="utf-8"))


@mailbox_app.command("list")
def list_cmd(ctx: typer.Context) -> None:
    client = get_client()
    emit(
        client.get("mail-connector", "mailbox-config"),
        output_format=output_format(ctx),
        columns=[
            "id",
            "name",
            "kind",
            "inbound_protocol",
            "host",
            "enabled",
            "last_poll_ok",
            "message_count",
        ],
    )


@mailbox_app.command("test")
def test(ctx: typer.Context, file: Path = typer.Argument(..., exists=True)) -> None:
    """Prueft die Verbindungsdaten, ohne etwas zu speichern."""
    client = get_client()
    emit(
        client.post("mail-connector", "mailbox-config/test", json_body=_read(file)),
        output_format=output_format(ctx),
    )


@mailbox_app.command("create")
def create(ctx: typer.Context, file: Path = typer.Argument(..., exists=True)) -> None:
    client = get_client()
    emit(
        client.post("mail-connector", "mailbox-config", json_body=_read(file)),
        output_format=output_format(ctx),
    )


@mailbox_app.command("update")
def update(
    ctx: typer.Context, mailbox_id: str, file: Path = typer.Argument(..., exists=True)
) -> None:
    client = get_client()
    emit(
        client.put("mail-connector", f"mailbox-config/{mailbox_id}", json_body=_read(file)),
        output_format=output_format(ctx),
    )


@mailbox_app.command("delete")
def delete(ctx: typer.Context, mailbox_id: str) -> None:
    client = get_client()
    client.delete("mail-connector", f"mailbox-config/{mailbox_id}")
    typer.echo(f"Postfach {mailbox_id} entfernt.")
