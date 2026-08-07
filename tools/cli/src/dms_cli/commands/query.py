"""Query & Trace Konsole (6.1) - dieselben `query-service`/`permission-service`-
Endpunkte wie die Admin-UIs `QueryConsoleView` (P8-S1/S2/S2b): Lesezugriff,
Schutzschalter, Dry-Run/Execute, Vier-Augen-Genehmigungen. Konzept 6.1 verlangt
woertlich, dass "dieselbe Abfragesprache und dieselben Sicherungsstufen" in
UI und CLI gelten - deshalb hier keine Abkuerzung gegenueber der Admin-UI."""

from __future__ import annotations

import json

import typer

from dms_cli import credentials
from dms_cli.context import get_client, output_format
from dms_cli.output import emit, print_json, print_table

# Exakter Spiegel von query_service.manipulation.ACTIONS und admin-ui's
# MANIPULATION_ACTION_TYPES - das CLI importiert absichtlich keinen
# Service-Code (Service-Isolation, siehe CONTRIBUTING.md), sondern haelt
# dieselbe kuratierte Liste wie die Admin-UI dupliziert.
_KNOWN_MANIPULATION_ACTION_TYPES = {
    "document.attribute_reset",
    "permission.role_assignment.delete",
    "object_type.update",
}

query_app = typer.Typer(help="Query & Trace Konsole (6.1).")
events_app = typer.Typer(help="Lesender Zugriff auf das Audit-Ereignisprotokoll.")
manipulation_mode_app = typer.Typer(help="Schutzschalter fuer den Manipulationsmodus.")
manipulate_app = typer.Typer(help="Dry-Run/Ausfuehrung kuratierter Manipulations-Aktionen.")
approvals_app = typer.Typer(help="Vier-Augen-Genehmigungen fuer Manipulations-Aktionen.")
query_app.add_typer(events_app, name="events")
query_app.add_typer(manipulation_mode_app, name="manipulation-mode")
query_app.add_typer(manipulate_app, name="manipulate")
query_app.add_typer(approvals_app, name="approvals")


def register(app: typer.Typer) -> None:
    app.add_typer(query_app, name="query")


def _parse_params(params: list[str]) -> dict:
    result: dict = {}
    for item in params:
        if "=" not in item:
            typer.echo(f"Ungueltiger --param Wert (erwarte key=value): {item!r}", err=True)
            raise typer.Exit(code=2)
        key, raw_value = item.split("=", 1)
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        result[key] = value
    return result


@events_app.command("list")
def events_list(
    ctx: typer.Context,
    actor: str | None = typer.Option(None, "--actor"),
    subject: str | None = typer.Option(None, "--subject"),
    event_type: str | None = typer.Option(None, "--event-type"),
    since: str | None = typer.Option(None, "--since", help="ISO 8601, z. B. 2026-08-01T00:00:00"),
    until: str | None = typer.Option(None, "--until", help="ISO 8601"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    client = get_client()
    result = client.get(
        "query-service",
        "query/events",
        params={
            "actor": actor,
            "subject": subject,
            "event_type": event_type,
            "since": since,
            "until": until,
            "limit": limit,
        },
    )
    if output_format(ctx) == "json":
        print_json(result)
        return
    print_table(result.get("events", []))
    typer.echo(
        f"{result.get('total_after_filter')}/{result.get('total_before_filter')} Ereignisse "
        f"(superuser={result.get('superuser')})"
    )


@query_app.command("text")
def query_text(ctx: typer.Context, query_text: str = typer.Argument(...)) -> None:
    client = get_client()
    result = client.post("query-service", "query", json_body={"query_text": query_text})
    emit(result, output_format=output_format(ctx))


@manipulation_mode_app.command("status")
def manipulation_mode_status(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("query-service", "manipulation-mode/status")
    emit(result, output_format=output_format(ctx))


@manipulation_mode_app.command("activate")
def manipulation_mode_activate(
    ctx: typer.Context, minutes: int = typer.Option(15, "--minutes")
) -> None:
    client = get_client()
    result = client.post(
        "query-service", "manipulation-mode/activate", json_body={"duration_minutes": minutes}
    )
    emit(result, output_format=output_format(ctx))


@manipulation_mode_app.command("deactivate")
def manipulation_mode_deactivate(ctx: typer.Context) -> None:
    client = get_client()
    result = client.post("query-service", "manipulation-mode/deactivate")
    emit(result, output_format=output_format(ctx))


@manipulate_app.command("dry-run")
def manipulate_dry_run(
    ctx: typer.Context,
    action: str = typer.Option(
        ..., "--action", help=", ".join(sorted(_KNOWN_MANIPULATION_ACTION_TYPES))
    ),
    param: list[str] = typer.Option(
        [], "--param", help="key=value, wiederholbar; Wert wird als JSON geparst wenn moeglich."
    ),
) -> None:
    client = get_client()
    result = client.post(
        "query-service",
        "manipulate/dry-run",
        json_body={"action_type": action, "params": _parse_params(param)},
    )
    emit(result, output_format=output_format(ctx))


@manipulate_app.command("execute")
def manipulate_execute(
    ctx: typer.Context, dry_run_token: str = typer.Option(..., "--dry-run-token")
) -> None:
    client = get_client()
    result = client.post(
        "query-service", "manipulate/execute", json_body={"dry_run_token": dry_run_token}
    )
    emit(result, output_format=output_format(ctx))


@approvals_app.command("list")
def approvals_list(ctx: typer.Context) -> None:
    client = get_client()
    all_requests = client.get(
        "permission-service", "approval-requests", params={"status": "pending"}
    )
    filtered = [r for r in all_requests if r.get("action_type") in _KNOWN_MANIPULATION_ACTION_TYPES]
    emit(
        filtered,
        output_format=output_format(ctx),
        columns=["id", "action_type", "initiated_by", "created_at", "payload"],
    )


@approvals_app.command("approve")
def approvals_approve(
    ctx: typer.Context,
    request_id: str = typer.Argument(...),
    approved_by: str | None = typer.Option(None, "--approved-by"),
) -> None:
    client = get_client()
    if approved_by is None:
        creds = credentials.load_credentials()
        approved_by = (creds.username if creds else None) or "unknown"
    result = client.post(
        "permission-service",
        f"approval-requests/{request_id}/approve",
        json_body={"approved_by": approved_by},
    )
    emit(result, output_format=output_format(ctx))
