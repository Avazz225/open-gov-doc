"""Four-eyes requests of every action type (P96-S2) - permission-service
`/approval-requests`. `dms query approvals` stays the query console's view."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

approvals_app = typer.Typer(help="Vier-Augen-Antraege aller Aktionstypen (permission-service).")


def register(app: typer.Typer) -> None:
    app.add_typer(approvals_app, name="approvals")


@approvals_app.command("list")
def list_cmd(
    ctx: typer.Context,
    status: str = typer.Option("pending", "--status", help="pending, approved, rejected oder all"),
    action_type: str | None = typer.Option(None, "--action-type"),
) -> None:
    client = get_client()
    result = client.get(
        "permission-service",
        "approval-requests",
        params={"status": None if status == "all" else status, "action_type": action_type},
    )
    emit(
        result,
        output_format=output_format(ctx),
        columns=["id", "action_type", "status", "initiated_by", "created_at"],
    )


@approvals_app.command("show")
def show(ctx: typer.Context, request_id: str) -> None:
    client = get_client()
    emit(
        client.get("permission-service", f"approval-requests/{request_id}"),
        output_format=output_format(ctx),
    )


@approvals_app.command("approve")
def approve(ctx: typer.Context, request_id: str) -> None:
    """Gibt einen Antrag frei - nur durch eine zweite Person, nie durch den Antragsteller."""
    # The approver is the logged-in identity (ADR 0240); the body is empty.
    client = get_client()
    result = client.post(
        "permission-service", f"approval-requests/{request_id}/approve", json_body={}
    )
    emit(result, output_format=output_format(ctx))


@approvals_app.command("reject")
def reject(
    ctx: typer.Context,
    request_id: str,
    reason: str = typer.Option(..., "--reason"),
) -> None:
    client = get_client()
    result = client.post(
        "permission-service",
        f"approval-requests/{request_id}/reject",
        json_body={"reason": reason},
    )
    emit(result, output_format=output_format(ctx))
