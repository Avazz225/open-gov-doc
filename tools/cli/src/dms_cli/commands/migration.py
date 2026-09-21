"""Installationsuebergreifende Migrations-/Transfer-Vorgaenge (7.2,
`migration-service`) - P67-S2: previously not wired into the CLI at all
despite the service existing for many phases, see docs/tools/cli.md
"Offene Punkte"."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

migration_app = typer.Typer(help="Installationsuebergreifende Migration (7.2).")
installations_app = typer.Typer(help="Gepaarte Zielinstallationen.")
transfers_app = typer.Typer(help="Transfer-Vorgaenge.")
migration_app.add_typer(installations_app, name="installations")
migration_app.add_typer(transfers_app, name="transfers")


def register(app: typer.Typer) -> None:
    app.add_typer(migration_app, name="migration")


@installations_app.command("list")
def installations_list(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("migration-service", "paired-installations")
    emit(
        result,
        output_format=output_format(ctx),
        columns=["id", "display_name", "base_url", "created_at"],
    )


@installations_app.command("pair")
def installations_pair(
    ctx: typer.Context,
    display_name: str = typer.Option(..., "--display-name"),
    base_url: str = typer.Option(..., "--base-url"),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="Leer lassen, wenn DIESE Installation die Paarung initiiert (ein neuer "
        "Schluessel wird einmalig erzeugt und zurueckgegeben) - setzen, wenn der "
        "Schluessel bereits von der Gegenseite ausgestellt wurde.",
    ),
) -> None:
    client = get_client()
    result = client.post(
        "migration-service",
        "paired-installations",
        json_body={"display_name": display_name, "base_url": base_url, "api_key": api_key},
    )
    emit(result, output_format=output_format(ctx))
    if "api_key" in result:
        typer.echo(
            "Achtung: Dieser Schluessel wird nur jetzt angezeigt, danach nicht mehr abrufbar.",
            err=True,
        )


@installations_app.command("unpair")
def installations_unpair(installation_id: str) -> None:
    client = get_client()
    client.delete("migration-service", f"paired-installations/{installation_id}")
    typer.echo(f"Installation {installation_id!r} entpaart.")


@transfers_app.command("list")
def transfers_list(ctx: typer.Context, status: str | None = typer.Option(None, "--status")) -> None:
    client = get_client()
    result = client.get("migration-service", "transfers", params={"status": status})
    emit(
        result,
        output_format=output_format(ctx),
        columns=[
            "id",
            "source_folder_id",
            "target_installation_id",
            "status",
            "documents_copied",
            "documents_total",
        ],
    )


@transfers_app.command("get")
def transfers_get(ctx: typer.Context, transfer_id: str) -> None:
    client = get_client()
    result = client.get("migration-service", f"transfers/{transfer_id}")
    emit(result, output_format=output_format(ctx))


@transfers_app.command("create")
def transfers_create(
    ctx: typer.Context,
    source_folder_id: str = typer.Option(..., "--source-folder-id"),
    target_installation_id: str = typer.Option(..., "--target-installation-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    retention_days: int | None = typer.Option(None, "--retention-days"),
) -> None:
    client = get_client()
    result = client.post(
        "migration-service",
        "transfers",
        json_body={
            "source_folder_id": source_folder_id,
            "target_installation_id": target_installation_id,
            "dry_run": dry_run,
            "retention_days": retention_days,
        },
    )
    emit(result, output_format=output_format(ctx))
    if result.get("status") == "pending_approval":
        typer.echo(
            f"Vier-Augen-Prinzip aktiv - wartet auf Genehmigung "
            f"(approval_request_id={result.get('approval_request_id')}).",
            err=True,
        )
