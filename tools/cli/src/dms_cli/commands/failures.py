"""Failed processing (P96-S2): renditions, OCR results, notifications -
list and retry. Archival transfers have their own commands (`dms archival`)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import typer

from dms_cli.client import ApiError
from dms_cli.context import get_client, output_format
from dms_cli.output import emit

failures_app = typer.Typer(help="Fehlgeschlagene Verarbeitungen anzeigen und erneut anstossen.")

# kind -> (service, list path, retry path template, failed states)
_KINDS = {
    "rendition": (
        "rendering-service",
        "renditions",
        "renditions/{id}/retry",
        ("failed", "failed_permanent"),
    ),
    "ocr": ("ocr-service", "ocr-results", "ocr-results/{id}/retry", ("failed", "failed_permanent")),
    "notification": (
        "notification-service",
        "notifications",
        "notifications/{id}/retry",
        ("failed",),
    ),
}


def register(app: typer.Typer) -> None:
    app.add_typer(failures_app, name="failures")


_DURATION = re.compile(r"^(\d+)([hd])$")


def _parse_since(value: str) -> datetime:
    """`24h`, `7d`, a date (`2026-11-20`) or an ISO timestamp."""
    text = value.strip()
    match = _DURATION.match(text.lower())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return datetime.now(UTC) - (
            timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(
            f"--since: 24h, 7d, ein Datum oder ein ISO-Zeitstempel, nicht {value!r}"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _when(entry: dict) -> str | None:
    # The last attempt where the service records it (renditions, OCR);
    # notifications only know when they were created.
    return entry.get("updated_at") or entry.get("created_at")


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@failures_app.command("list")
def list_cmd(
    ctx: typer.Context,
    kind: str | None = typer.Option(None, "--kind", help="rendition, ocr oder notification"),
    status: str | None = typer.Option(
        None, "--status", help="failed (wird automatisch wiederholt) oder failed_permanent"
    ),
    since: str | None = typer.Option(
        None, "--since", help="Nur seit: 24h, 7d, ein Datum oder ein ISO-Zeitstempel."
    ),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Höchstens so viele (neueste)."),
    group: bool = typer.Option(
        False, "--group", help="Nach Art, Status und Fehlermeldung zählen statt einzeln listen."
    ),
) -> None:
    """Fehlgeschlagene Verarbeitungen, neueste zuerst (P98-S1: mit Zeitpunkt)."""
    since_at = _parse_since(since) if since else None
    client = get_client()
    rows = []
    for name, (service, path, _retry, states) in _KINDS.items():
        if kind and kind != name:
            continue
        for state in states:
            if status and status != state:
                continue
            try:
                entries = client.get(service, path, params={"status": state})
            except ApiError as exc:
                # One source the session may not read doesn't hide the others.
                typer.echo(f"{name}: nicht lesbar - {exc}", err=True)
                break
            for entry in entries:
                when = _when(entry)
                if since_at and (_as_datetime(when) or since_at) < since_at:
                    continue
                rows.append(
                    {
                        "when": when,
                        "kind": name,
                        "id": entry.get("id"),
                        "status": entry.get("status"),
                        "subject": entry.get("document_id") or entry.get("recipient"),
                        "attempts": entry.get("attempts"),
                        "error": (entry.get("error_message") or entry.get("error") or "")[:80],
                    }
                )
    rows.sort(key=lambda row: row["when"] or "", reverse=True)
    if group:
        counts: dict[tuple, dict] = {}
        for row in rows:
            key = (row["kind"], row["status"], row["error"])
            bucket = counts.setdefault(
                key,
                {
                    "count": 0,
                    "kind": row["kind"],
                    "status": row["status"],
                    "error": row["error"],
                    "latest": row["when"],
                },
            )
            bucket["count"] += 1
        grouped = sorted(counts.values(), key=lambda b: b["count"], reverse=True)
        emit(grouped, output_format=output_format(ctx))
        return
    if limit:
        rows = rows[:limit]
    emit(rows, output_format=output_format(ctx))


@failures_app.command("retry")
def retry(ctx: typer.Context, kind: str, item_id: str) -> None:
    if kind not in _KINDS:
        typer.echo(f"Unbekannte Art {kind!r} - erlaubt: {', '.join(_KINDS)}", err=True)
        raise typer.Exit(code=2)
    service, _path, retry_path, _states = _KINDS[kind]
    client = get_client()
    emit(
        client.post(service, retry_path.format(id=item_id)),
        output_format=output_format(ctx),
    )
