"""Monitoring (P96-S2): sensors, alert rules with preview and state,
downtime windows - monitoring-service."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

monitoring_app = typer.Typer(help="Sensoren, Alarmregeln, Wartungsfenster (monitoring-service).")
rule_app = typer.Typer(help="Alarmregeln.")
downtime_app = typer.Typer(help="Wartungsfenster (Alarme unterdrueckt).")
monitoring_app.add_typer(rule_app, name="alert-rule")
monitoring_app.add_typer(downtime_app, name="downtime")


def register(app: typer.Typer) -> None:
    app.add_typer(monitoring_app, name="monitoring")


def _read(file: Path) -> dict:
    return json.loads(file.read_text(encoding="utf-8"))


def _rule_by_name_or_id(client, rule: str) -> dict:
    for entry in client.get("monitoring-service", "alert-rules"):
        if rule in (entry["id"], entry["name"]):
            return entry
    typer.echo(f"Alarmregel {rule!r} nicht gefunden.", err=True)
    raise typer.Exit(code=1)


@monitoring_app.command("sensors")
def sensors(ctx: typer.Context) -> None:
    """Der Sensorkatalog mit Gruppe, Kosten und Aktivierung."""
    client = get_client()
    emit(
        client.get("monitoring-service", "sensors"),
        output_format=output_format(ctx),
    )


@monitoring_app.command("alerts")
def alerts(ctx: typer.Context, all_rules: bool = typer.Option(False, "--all")) -> None:
    """Aktueller Zustand der Alarmregeln - Default nur warning/error."""
    client = get_client()
    rules = client.get("monitoring-service", "alert-rules")
    if not all_rules:
        rules = [r for r in rules if r.get("level") in ("warning", "error")]
    emit(
        rules,
        output_format=output_format(ctx),
        columns=["name", "sensor", "level", "value", "since", "suppressed"],
    )


@rule_app.command("list")
def rule_list(ctx: typer.Context) -> None:
    client = get_client()
    emit(
        client.get("monitoring-service", "alert-rules"),
        output_format=output_format(ctx),
        columns=[
            "id",
            "name",
            "sensor",
            "aggregation",
            "max_warning",
            "max_error",
            "enabled",
            "level",
        ],
    )


@rule_app.command("preview")
def rule_preview(
    ctx: typer.Context,
    file: Path = typer.Argument(
        ..., exists=True, help="Regel als JSON (Felder wie alert-rule create)."
    ),
) -> None:
    """Wertet einen Regelentwurf gegen die aktuellen Werte aus, speichert nichts."""
    client = get_client()
    emit(
        client.post("monitoring-service", "alert-rules/preview", json_body=_read(file)),
        output_format=output_format(ctx),
    )


@rule_app.command("create")
def rule_create(ctx: typer.Context, file: Path = typer.Argument(..., exists=True)) -> None:
    client = get_client()
    emit(
        client.post("monitoring-service", "alert-rules", json_body=_read(file)),
        output_format=output_format(ctx),
    )


@rule_app.command("update")
def rule_update(
    ctx: typer.Context,
    rule: str = typer.Argument(..., help="ID oder Name"),
    file: Path = typer.Argument(..., exists=True),
) -> None:
    client = get_client()
    entry = _rule_by_name_or_id(client, rule)
    emit(
        client.put("monitoring-service", f"alert-rules/{entry['id']}", json_body=_read(file)),
        output_format=output_format(ctx),
    )


@rule_app.command("delete")
def rule_delete(ctx: typer.Context, rule: str = typer.Argument(..., help="ID oder Name")) -> None:
    client = get_client()
    entry = _rule_by_name_or_id(client, rule)
    client.delete("monitoring-service", f"alert-rules/{entry['id']}")
    typer.echo(f"Alarmregel {entry['name']} geloescht.")


@downtime_app.command("list")
def downtime_list(ctx: typer.Context) -> None:
    client = get_client()
    emit(
        client.get("monitoring-service", "downtime-windows"),
        output_format=output_format(ctx),
        columns=[
            "id",
            "name",
            "active",
            "starts_at",
            "ends_at",
            "weekdays",
            "daily_start",
            "daily_end",
        ],
    )


@downtime_app.command("create")
def downtime_create(ctx: typer.Context, file: Path = typer.Argument(..., exists=True)) -> None:
    client = get_client()
    emit(
        client.post("monitoring-service", "downtime-windows", json_body=_read(file)),
        output_format=output_format(ctx),
    )


@downtime_app.command("delete")
def downtime_delete(ctx: typer.Context, window_id: str) -> None:
    client = get_client()
    client.delete("monitoring-service", f"downtime-windows/{window_id}")
    typer.echo(f"Wartungsfenster {window_id} geloescht.")
