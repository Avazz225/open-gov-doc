"""Loeschregister-Einsicht + Wiederherstellungs-Abgleich (10.4, `document-
service`/`folder-service`) - P67-S2: previously not wired into the CLI at
all, see docs/tools/cli.md "Offene Punkte". Backup/Restore selbst (10.4's
andere Haelfte) hat bewusst KEINE HTTP-Schnittstelle (reine Host-Skripte,
siehe docs/operations/backup-restore.md) und bleibt daher ausserhalb der
CLI - dieses Modul deckt nur die Loeschregister-Abgleich-Haelfte ab, die
tatsaechlich ueber das Gateway erreichbar ist."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

_SERVICE_BY_KIND: dict[str, str] = {
    "document": "document-service",
    "folder": "folder-service",
}
_RESOURCE_BY_KIND: dict[str, str] = {
    "document": "documents",
    "folder": "folders",
}

deletion_register_app = typer.Typer(
    help="Loeschregister-Einsicht + Wiederherstellungs-Abgleich (10.4)."
)


def register(app: typer.Typer) -> None:
    app.add_typer(deletion_register_app, name="deletion-register")


def _resolve_service(kind: str) -> str:
    if kind not in _SERVICE_BY_KIND:
        typer.echo(f"--kind muss 'document' oder 'folder' sein, nicht {kind!r}.", err=True)
        raise typer.Exit(code=2)
    return _SERVICE_BY_KIND[kind]


@deletion_register_app.command("list")
def list_cmd(
    ctx: typer.Context,
    kind: str = typer.Option("document", "--kind", help="'document' oder 'folder'."),
    resource_id: str | None = typer.Option(
        None, "--id", help="Nur Eintraege dieser document_id/folder_id."
    ),
) -> None:
    client = get_client()
    service = _resolve_service(kind)
    id_param = "document_id" if kind == "document" else "folder_id"
    result = client.get(service, "deletion-register", params={id_param: resource_id})
    emit(
        result,
        output_format=output_format(ctx),
        columns=["id", id_param, "trigger", "reason", "triggered_by", "occurred_at"],
    )


@deletion_register_app.command("reconcile")
def reconcile_cmd(
    ctx: typer.Context,
    resource_id: str,
    original_entry_id: str = typer.Option(..., "--original-entry-id"),
    kind: str = typer.Option("document", "--kind", help="'document' oder 'folder'."),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Meldet an den jeweiligen Dienst, dass eine zuvor geloeschte
    Ressource durch ein Restore ausserhalb des Systems wiederhergestellt
    wurde - erfordert die Domain-Admin-Rolle des Dienstes."""
    client = get_client()
    service = _resolve_service(kind)
    resource_path = _RESOURCE_BY_KIND[kind]
    client.post(
        service,
        f"{resource_path}/{resource_id}/reconcile-restore-deletion",
        json_body={"original_entry_id": original_entry_id, "reason": reason},
    )
    typer.echo(f"{kind.capitalize()} {resource_id!r} als wiederhergestellt vermerkt.")
    if output_format(ctx) == "json":
        emit({"status": "reconciled", "id": resource_id}, output_format="json")
