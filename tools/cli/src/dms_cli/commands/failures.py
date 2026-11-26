"""Failed processing (P96-S2): renditions, OCR results, notifications -
list and retry. Archival transfers have their own commands (`dms archival`)."""

from __future__ import annotations

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


@failures_app.command("list")
def list_cmd(
    ctx: typer.Context,
    kind: str | None = typer.Option(None, "--kind", help="rendition, ocr oder notification"),
) -> None:
    client = get_client()
    rows = []
    for name, (service, path, _retry, states) in _KINDS.items():
        if kind and kind != name:
            continue
        for state in states:
            try:
                entries = client.get(service, path, params={"status": state})
            except ApiError as exc:
                # One source the session may not read doesn't hide the others.
                typer.echo(f"{name}: nicht lesbar - {exc}", err=True)
                break
            for entry in entries:
                rows.append(
                    {
                        "kind": name,
                        "id": entry.get("id"),
                        "status": entry.get("status"),
                        "subject": entry.get("document_id") or entry.get("recipient"),
                        "attempts": entry.get("attempts"),
                        "error": (entry.get("error_message") or entry.get("error") or "")[:80],
                    }
                )
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
