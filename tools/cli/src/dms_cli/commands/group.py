"""Groups and memberships (P96-S2) - permission-service `/groups`."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

group_app = typer.Typer(help="Gruppen und Mitgliedschaften (permission-service).")


def register(app: typer.Typer) -> None:
    app.add_typer(group_app, name="group")


def _resolve(client, group: str) -> dict:
    """A group by id or by name."""
    for entry in client.get("permission-service", "groups"):
        if group in (entry["id"], entry["name"]):
            return entry
    typer.echo(f"Gruppe {group!r} nicht gefunden.", err=True)
    raise typer.Exit(code=1)


@group_app.command("list")
def list_cmd(ctx: typer.Context) -> None:
    client = get_client()
    emit(
        client.get("permission-service", "groups"),
        output_format=output_format(ctx),
        columns=["id", "name", "is_org_unit", "description"],
    )


@group_app.command("create")
def create(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    description: str = typer.Option("", "--description"),
    org_unit: bool = typer.Option(False, "--org-unit", help="Als Organisationseinheit anlegen."),
) -> None:
    client = get_client()
    result = client.post(
        "permission-service",
        "groups",
        json_body={"name": name, "description": description, "is_org_unit": org_unit},
    )
    emit(result, output_format=output_format(ctx))


@group_app.command("members")
def members(ctx: typer.Context, group: str = typer.Argument(..., help="ID oder Name")) -> None:
    client = get_client()
    entry = _resolve(client, group)
    emit(
        client.get("permission-service", f"groups/{entry['id']}/members"),
        output_format=output_format(ctx),
    )


@group_app.command("add")
def add(
    ctx: typer.Context,
    group: str = typer.Argument(..., help="ID oder Name"),
    principal_id: str = typer.Argument(...),
) -> None:
    client = get_client()
    entry = _resolve(client, group)
    client.post(
        "permission-service",
        f"groups/{entry['id']}/members",
        json_body={"principal_id": principal_id},
    )
    typer.echo(f"{principal_id} ist jetzt Mitglied von {entry['name']}.")


@group_app.command("remove")
def remove(
    ctx: typer.Context,
    group: str = typer.Argument(..., help="ID oder Name"),
    principal_id: str = typer.Argument(...),
) -> None:
    client = get_client()
    entry = _resolve(client, group)
    client.delete("permission-service", f"groups/{entry['id']}/members/{principal_id}")
    typer.echo(f"{principal_id} aus {entry['name']} entfernt.")


@group_app.command("delete")
def delete(ctx: typer.Context, group: str = typer.Argument(..., help="ID oder Name")) -> None:
    client = get_client()
    entry = _resolve(client, group)
    client.delete("permission-service", f"groups/{entry['id']}")
    typer.echo(f"Gruppe {entry['name']} geloescht.")
