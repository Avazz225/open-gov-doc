"""Retention settings (P96-S2): trash period, deletion reasons, reminders.
document-service and folder-service each hold a copy (ADR 0233); the CLI
reads both and writes both, as admin-ui does."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

retention_app = typer.Typer(help="Aufbewahrung und Papierkorb (document-/folder-service).")
_OWNERS = ("document-service", "folder-service")


def register(app: typer.Typer) -> None:
    app.add_typer(retention_app, name="retention")


@retention_app.command("show")
def show(ctx: typer.Context) -> None:
    client = get_client()
    rows = []
    for owner in _OWNERS:
        retention = client.get(owner, "retention-config")
        trash = client.get(owner, "trash-config")
        rows.append(
            {
                "service": owner,
                "trash_days": trash.get("restore_period_days"),
                "reason_required": retention.get("deletion_reason_required"),
                "reminder_lead_days": retention.get("reminder_lead_days"),
                "deletion_reasons": retention.get("deletion_reason_catalog"),
            }
        )
    emit(rows, output_format=output_format(ctx))


@retention_app.command("set")
def set_cmd(
    ctx: typer.Context,
    trash_days: int | None = typer.Option(None, "--trash-days"),
    reason_required: bool | None = typer.Option(None, "--reason-required/--no-reason-required"),
    reminder_lead_days: int | None = typer.Option(None, "--reminder-lead-days"),
    add_reason: list[str] = typer.Option([], "--add-reason", help="Wiederholbar."),
) -> None:
    """Aendert nur die angegebenen Werte, auf beiden Diensten."""
    client = get_client()
    for owner in _OWNERS:
        if trash_days is not None:
            client.put(owner, "trash-config", json_body={"restore_period_days": trash_days})
        if reason_required is not None or reminder_lead_days is not None or add_reason:
            current = client.get(owner, "retention-config")
            body = {
                "deletion_reason_required": current.get("deletion_reason_required", False),
                "reminder_lead_days": current.get("reminder_lead_days"),
                "deletion_reason_catalog": list(current.get("deletion_reason_catalog") or []),
            }
            if reason_required is not None:
                body["deletion_reason_required"] = reason_required
            if reminder_lead_days is not None:
                body["reminder_lead_days"] = reminder_lead_days
            for reason in add_reason:
                if reason not in body["deletion_reason_catalog"]:
                    body["deletion_reason_catalog"].append(reason)
            client.put(owner, "retention-config", json_body=body)
    show(ctx)
