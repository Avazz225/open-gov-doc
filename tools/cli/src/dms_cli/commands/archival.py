"""Aussonderungs-Transfers (5.6, `archival-service`) - naechstliegende reale
Entsprechung zu Konzept 6.2s "Migrations-/Transfer-Vorgaengen (7.2)": der
generische 7.2-Migrationsdienst existiert noch nicht (Phase 12), siehe
docs/tools/cli.md "Offene Punkte"."""

from __future__ import annotations

from pathlib import Path

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

archival_app = typer.Typer(help="Aussonderungs-Transfers (5.6).")
transfers_app = typer.Typer(help="Dokument-Aussonderungstransfers.")
case_transfers_app = typer.Typer(help="Umlaufmappen-Aussonderungstransfers (XDOMEA).")
archival_app.add_typer(transfers_app, name="transfers")
archival_app.add_typer(case_transfers_app, name="case-transfers")


def register(app: typer.Typer) -> None:
    app.add_typer(archival_app, name="archival")


@transfers_app.command("list")
def transfers_list(ctx: typer.Context, status: str | None = typer.Option(None, "--status")) -> None:
    client = get_client()
    result = client.get("archival-service", "archival-transfers", params={"status": status})
    emit(
        result,
        output_format=output_format(ctx),
        columns=["id", "document_id", "status", "created_at"],
    )


@transfers_app.command("get")
def transfers_get(ctx: typer.Context, transfer_id: str) -> None:
    client = get_client()
    result = client.get("archival-service", f"archival-transfers/{transfer_id}")
    emit(result, output_format=output_format(ctx))


@transfers_app.command("retrieve")
def transfers_retrieve(ctx: typer.Context, transfer_id: str) -> None:
    client = get_client()
    result = client.post("archival-service", f"archival-transfers/{transfer_id}/retrieve")
    emit(result, output_format=output_format(ctx))


@case_transfers_app.command("list")
def case_transfers_list(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("archival-service", "case-archival-transfers")
    emit(
        result, output_format=output_format(ctx), columns=["id", "case_id", "status", "created_at"]
    )


@case_transfers_app.command("get")
def case_transfers_get(ctx: typer.Context, transfer_id: str) -> None:
    client = get_client()
    result = client.get("archival-service", f"case-archival-transfers/{transfer_id}")
    emit(result, output_format=output_format(ctx))


@case_transfers_app.command("package")
def case_transfers_package(
    ctx: typer.Context,
    transfer_id: str,
    out: Path = typer.Option(..., "--out", help="Zielpfad fuer das XDOMEA-ZIP-Paket."),
) -> None:
    client = get_client()
    content = client.get_bytes("archival-service", f"case-archival-transfers/{transfer_id}/package")
    out.write_bytes(content)
    typer.echo(f"Paket gespeichert: {out}")
