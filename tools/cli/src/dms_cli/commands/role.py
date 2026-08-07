"""Rollen/Rollenzuweisungen (4.1/4.6) - Wrapper um permission-service
`/roles` + `/role-assignments`."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

role_app = typer.Typer(help="Rollen/Rollenzuweisungen (permission-service).")
assignment_app = typer.Typer(help="Rollenzuweisungen.")
role_app.add_typer(assignment_app, name="assignment")


def register(app: typer.Typer) -> None:
    app.add_typer(role_app, name="role")


@role_app.command("list")
def list_cmd(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("permission-service", "roles")
    emit(result, output_format=output_format(ctx), columns=["id", "name", "permissions"])


@assignment_app.command("list")
def assignment_list(
    ctx: typer.Context,
    principal_id: str | None = typer.Option(None, "--principal"),
    resource_id: str | None = typer.Option(None, "--resource"),
) -> None:
    client = get_client()
    result = client.get(
        "permission-service",
        "role-assignments",
        params={"principal_id": principal_id, "resource_id": resource_id},
    )
    emit(result, output_format=output_format(ctx))


@assignment_app.command("create")
def assignment_create(
    ctx: typer.Context,
    principal_type: str = typer.Option(..., "--principal-type"),
    principal_id: str = typer.Option(..., "--principal"),
    role_id: int = typer.Option(..., "--role"),
    resource_id: str = typer.Option(..., "--resource"),
) -> None:
    client = get_client()
    result = client.post(
        "permission-service",
        "role-assignments",
        json_body={
            "principal_type": principal_type,
            "principal_id": principal_id,
            "role_id": role_id,
            "resource_id": resource_id,
        },
    )
    emit(result, output_format=output_format(ctx))


@assignment_app.command("delete")
def assignment_delete(ctx: typer.Context, assignment_id: int) -> None:
    client = get_client()
    client.delete("permission-service", f"role-assignments/{assignment_id}")
    typer.echo(f"Rollenzuweisung {assignment_id} entfernt.")
