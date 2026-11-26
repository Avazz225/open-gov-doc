"""Central log levels (P97-S2, ADR 0267): show, set and reset them -
monitoring-service. Every replica of a service picks a change up with its
next configuration poll (at most 15 s)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit, print_record, print_table

logging_app = typer.Typer(
    help=(
        "Log-Level zentral setzen: global, je Dienst oder je Logger (monitoring-service). "
        "Alle Replikas übernehmen eine Änderung innerhalb von 15 s; DEBUG läuft immer ab."
    )
)

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m?)?$")


def register(app: typer.Typer) -> None:
    app.add_typer(logging_app, name="logging")


def parse_duration(value: str) -> int:
    """`30m`, `2h`, `1h30m` or plain minutes (`90`) -> minutes."""
    match = _DURATION.match(value.strip().lower())
    if not value.strip() or not match or not any(match.groups()):
        raise typer.BadParameter(f"Dauer wie 30m, 2h oder 1h30m angeben, nicht {value!r}")
    hours, minutes = (int(part) if part else 0 for part in match.groups())
    total = hours * 60 + minutes
    if total < 1:
        raise typer.BadParameter("Die Dauer muss mindestens 1 Minute sein")
    return total


def _remaining(expires_at: str | None) -> str:
    if not expires_at:
        return ""
    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    minutes = int((expiry - datetime.now(UTC)).total_seconds() // 60)
    return f"noch {minutes} min" if minutes >= 0 else "abgelaufen"


def _settings_rows(settings: list[dict]) -> list[dict]:
    return [
        {
            "bereich": s["scope"],
            "level": s["level"],
            "läuft ab": s["expires_at"] or "",
            "rest": _remaining(s["expires_at"]),
            "von": s["updated_by"],
        }
        for s in settings
    ]


@logging_app.command("show")
def show(
    ctx: typer.Context,
    service: str = typer.Option(None, "--service", help="Nur diesen Dienst zeigen."),
) -> None:
    """Was je Dienst gilt und woher es kommt (service/global/env = DMS_LOG_LEVEL)."""
    data = get_client().get("monitoring-service", "log-levels")
    if service:
        effective = data["effective"].get(service)
        if effective is None:
            typer.echo(f"Dienst {service!r} unbekannt.", err=True)
            raise typer.Exit(code=1)
        if output_format(ctx) == "json":
            emit({"service": service, **effective}, output_format="json")
            return
        print_record(
            {
                "dienst": service,
                "grund-level": effective["root_level"] or "DMS_LOG_LEVEL des Dienstes",
                "quelle": effective["root_source"],
            }
        )
        if effective["loggers"]:
            typer.echo("")
            print_table(
                [
                    {"logger": name, "level": v["level"], "quelle": v["source"]}
                    for name, v in effective["loggers"].items()
                ]
            )
        return
    if output_format(ctx) == "json":
        emit(data, output_format="json")
        return
    print_table(
        [
            {
                "dienst": name,
                "grund-level": v["root_level"] or "(DMS_LOG_LEVEL)",
                "quelle": v["root_source"],
                "logger": ", ".join(f"{k}={x['level']}" for k, x in v["loggers"].items()),
            }
            for name, v in data["effective"].items()
        ]
    )
    if data["settings"]:
        typer.echo("")
        typer.echo("Einstellungen:")
        print_table(_settings_rows(data["settings"]))


@logging_app.command("set")
def set_level(
    ctx: typer.Context,
    level: str = typer.Argument(..., help="DEBUG, INFO, WARNING, ERROR oder CRITICAL."),
    service: str = typer.Option(None, "--service", help="Dienst; ohne Angabe global."),
    logger: str = typer.Option(
        None, "--logger", help="Logger (mit Unterloggern); ohne --service in allen Diensten."
    ),
    duration: str = typer.Option(None, "--for", help="Gültig für z. B. 30m, 2h."),
    until: str = typer.Option(None, "--until", help="Gültig bis (ISO-Zeitstempel)."),
) -> None:
    """Einen Level setzen. DEBUG ohne Dauer gilt 60 min (höchstens 24 h)."""
    if duration and until:
        raise typer.BadParameter("Nur eines von --for oder --until angeben")
    body = {
        "level": level,
        "service": service,
        "logger": logger,
        "expires_in_minutes": parse_duration(duration) if duration else None,
        "expires_at": until,
    }
    data = get_client().put(
        "monitoring-service", "log-levels", json_body={k: v for k, v in body.items() if v}
    )
    scope = _scope_key(service, logger)
    setting = next((s for s in data["settings"] if s["scope"] == scope), None)
    if output_format(ctx) == "json":
        emit(setting, output_format="json")
        return
    typer.echo(f"Gesetzt: {scope} = {setting['level'] if setting else level}")
    if setting and setting["expires_at"]:
        typer.echo(f"Läuft ab: {setting['expires_at']} ({_remaining(setting['expires_at'])})")
    typer.echo("Alle Replikas übernehmen das innerhalb von 15 s.")


@logging_app.command("reset")
def reset(
    ctx: typer.Context,
    service: str = typer.Option(None, "--service"),
    logger: str = typer.Option(None, "--logger"),
) -> None:
    """Eine Einstellung entfernen; es gilt dann die nächst allgemeinere."""
    params = {"service": service, "logger": logger}
    data = get_client().delete("monitoring-service", "log-levels", params=params)
    if output_format(ctx) == "json":
        emit(data, output_format="json")
        return
    typer.echo(f"Entfernt: {_scope_key(service, logger)}")


@logging_app.command("loggers")
def loggers(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help="Dienst."),
    contains: str = typer.Option(None, "--filter", help="Nur Namen, die das enthalten."),
) -> None:
    """Die Logger, die ein Dienst kennt (aus seiner Registrierung)."""
    data = get_client().get("monitoring-service", "log-levels")
    if service not in data["catalog"]:
        typer.echo(f"Dienst {service!r} unbekannt.", err=True)
        raise typer.Exit(code=1)
    names = [n for n in data["catalog"][service] if not contains or contains in n]
    if output_format(ctx) == "json":
        emit(names, output_format="json")
        return
    for name in names:
        typer.echo(name)


def _scope_key(service: str | None, logger: str | None) -> str:
    if logger:
        return f"logger:{service or '*'}:{logger}"
    if service:
        return f"service:{service}"
    return "global"
