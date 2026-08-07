"""Objekttypen/Constraints (2.2) - duenne Wrapper um object-type-service.
`create`/`update` nehmen eine JSON-Datei (`ObjectTypeCreate`/`ObjectTypeUpdate`-
Body) statt einzelner Flags, da das Schema zu viele optionale, teils
verschachtelte Felder hat (attributes/naming_constraints/conditions/...) fuer
eine sinnvolle Flag-Oberflaeche."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

object_type_app = typer.Typer(help="Objekttypen/Constraints (2.2).")


def register(app: typer.Typer) -> None:
    app.add_typer(object_type_app, name="object-type")


@object_type_app.command("list")
def list_cmd(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("object-type-service", "object-types")
    emit(result, output_format=output_format(ctx), columns=["id", "name", "applies_to"])


@object_type_app.command("get")
def get_cmd(ctx: typer.Context, object_type_id: int) -> None:
    client = get_client()
    result = client.get("object-type-service", f"object-types/{object_type_id}")
    emit(result, output_format=output_format(ctx))


@object_type_app.command("create")
def create_cmd(
    ctx: typer.Context,
    file: Path = typer.Option(
        ..., "--file", "-f", exists=True, help="JSON-Datei mit ObjectTypeCreate-Body."
    ),
) -> None:
    client = get_client()
    payload = json.loads(file.read_text(encoding="utf-8"))
    result = client.post("object-type-service", "object-types", json_body=payload)
    emit(result, output_format=output_format(ctx))


@object_type_app.command("update")
def update_cmd(
    ctx: typer.Context,
    object_type_id: int,
    file: Path = typer.Option(
        ..., "--file", "-f", exists=True, help="JSON-Datei mit ObjectTypeUpdate-Body."
    ),
) -> None:
    client = get_client()
    payload = json.loads(file.read_text(encoding="utf-8"))
    result = client.put("object-type-service", f"object-types/{object_type_id}", json_body=payload)
    emit(result, output_format=output_format(ctx))


@object_type_app.command("delete")
def delete_cmd(ctx: typer.Context, object_type_id: int) -> None:
    client = get_client()
    client.delete("object-type-service", f"object-types/{object_type_id}")
    typer.echo(f"Objekttyp {object_type_id} geloescht.")
