"""Lizenzstatus (9.3, `license-service`) - P67-S2: previously not wired into
the CLI at all despite the service existing for many phases, see
docs/tools/cli.md "Offene Punkte"."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

license_app = typer.Typer(help="Lizenzstatus (9.3).")


def register(app: typer.Typer) -> None:
    app.add_typer(license_app, name="license")


@license_app.command("status")
def status_cmd(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("license-service", "license/status")
    emit(result, output_format=output_format(ctx))


@license_app.command("upload")
def upload(
    ctx: typer.Context,
    token: str = typer.Option(..., "--token", help="Signiertes Lizenz-Token als Zeichenkette."),
) -> None:
    client = get_client()
    result = client.post("license-service", "license", json_body={"license_token": token})
    emit(result, output_format=output_format(ctx))
