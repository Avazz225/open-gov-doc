"""Event bus (P99-S2, ADR 0270): dead letters - events a consumer gave up on
after every delivery attempt. List, inspect, replay to that consumer, or
discard with a reason. monitoring-service, `domain-admin-monitoring`."""

from __future__ import annotations

import json

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit, print_record

events_app = typer.Typer(help="Event-Bus: unzustellbare Ereignisse (Dead Letters).")
dead_letters_app = typer.Typer(
    help="Ereignisse, die ein Dienst nach allen Zustellversuchen nicht verarbeiten konnte."
)
events_app.add_typer(dead_letters_app, name="dead-letters")


def register(app: typer.Typer) -> None:
    app.add_typer(events_app, name="events")


@dead_letters_app.command("list")
def list_cmd(
    ctx: typer.Context,
    limit: int = typer.Option(200, "--limit", min=1, max=1000),
    consumer: str | None = typer.Option(None, "--consumer", help="Nur dieser Consumer."),
) -> None:
    """Neueste zuerst."""
    letters = get_client().get("monitoring-service", "dead-letters", params={"limit": limit})
    if consumer:
        letters = [x for x in letters if x["consumer"] == consumer]
    if output_format(ctx) != "json":
        # A database error runs to hundreds of characters; `show` has it all.
        letters = [
            {**x, "error": x["error"] if len(x["error"]) <= 90 else x["error"][:89] + "…"}
            for x in letters
        ]
    emit(
        letters,
        output_format=output_format(ctx),
        columns=["seq", "failed_at", "consumer", "subject", "deliveries", "error"],
    )


@dead_letters_app.command("show")
def show_cmd(ctx: typer.Context, seq: int = typer.Argument(..., help="Nummer aus `list`.")) -> None:
    """Ein Dead Letter mit dem ursprünglichen Ereignis."""
    detail = get_client().get("monitoring-service", f"dead-letters/{seq}")
    if output_format(ctx) == "json":
        emit(detail, output_format="json")
        return
    payload = detail.pop("payload")
    print_record(detail)
    typer.echo("")
    try:
        typer.echo(json.dumps(json.loads(payload), indent=2, ensure_ascii=False))
    except ValueError:
        typer.echo(payload)


@dead_letters_app.command("replay")
def replay_cmd(
    ctx: typer.Context,
    seq: int = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", help="Warum es jetzt klappen sollte."),
) -> None:
    """An genau den Consumer erneut zustellen, der aufgegeben hatte."""
    letter = get_client().post(
        "monitoring-service", f"dead-letters/{seq}/replay", json_body={"reason": reason}
    )
    if output_format(ctx) == "json":
        emit(letter, output_format="json")
        return
    typer.echo(
        f"Erneut zugestellt an {letter['consumer']} ({letter['subject']}). "
        "Scheitert es wieder, erscheint es als neuer Dead Letter."
    )


@dead_letters_app.command("discard")
def discard_cmd(
    ctx: typer.Context,
    seq: int = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", help="Warum das Ereignis verworfen wird."),
) -> None:
    """Endgültig verwerfen (protokolliert)."""
    letter = get_client().delete(
        "monitoring-service", f"dead-letters/{seq}", params={"reason": reason}
    )
    if output_format(ctx) == "json":
        emit(letter, output_format="json")
        return
    typer.echo(f"Verworfen: {letter['subject']} für {letter['consumer']}.")
