"""Lokale CLI-Konfiguration anzeigen/aendern (Gateway-URL) sowie Export/Delta-
Vergleich der Systemkonfiguration gegen `config-service` (7.3/7.5)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dms_cli import credentials
from dms_cli.context import get_client, output_format
from dms_cli.output import emit

config_app = typer.Typer(help="Gespeicherte Gateway-URL/Zugangsdaten anzeigen oder aendern.")


def register(app: typer.Typer) -> None:
    app.add_typer(config_app, name="config")


@config_app.command("show")
def show(ctx: typer.Context) -> None:
    creds = credentials.load_credentials()
    if creds is None:
        typer.echo("Keine gespeicherten Zugangsdaten - 'dms login' ausfuehren.", err=True)
        raise typer.Exit(code=1)
    data = {
        "gateway_url": creds.gateway_url,
        "username": creds.username,
        "angemeldet": bool(creds.access_token),
    }
    emit(data, output_format=output_format(ctx))


@config_app.command("set-gateway-url")
def set_gateway_url(url: str) -> None:
    creds = credentials.load_credentials() or credentials.Credentials(gateway_url=url)
    creds.gateway_url = url
    credentials.save_credentials(creds)
    typer.echo(f"Gateway-URL gesetzt: {url}")


@config_app.command("export")
def export_config(
    ctx: typer.Context,
    category: list[str] = typer.Option(
        [], "--category", help="Wiederholbar - Default: alle sechs Kategorien (7.3)."
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="In Datei statt stdout schreiben - Eingabe fuer 'config compare'.",
    ),
) -> None:
    client = get_client()
    result = client.get(
        "config-service", "config/export", params={"categories": category} if category else None
    )
    if file:
        file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        typer.echo(f"Export geschrieben nach {file}")
        return
    emit(result, output_format=output_format(ctx))


@config_app.command("compare")
def compare_config(
    ctx: typer.Context,
    compare: Path = typer.Argument(
        ..., exists=True, help="JSON-Export der Vergleichsinstanz, siehe 'config export' (7.5)."
    ),
    base: Path | None = typer.Option(
        None,
        "--base",
        exists=True,
        help="JSON-Export der Basisinstanz - fehlt dies, zieht config-service den eigenen "
        "aktuellen Stand heran (Anwendungsfall 'was wuerde sich beim Import aendern').",
    ),
    category: list[str] = typer.Option([], "--category", help="Wiederholbar - Default: alle."),
    ignore_regex: str | None = typer.Option(
        None,
        "--ignore-regex",
        help='JSON-Objekt Kategorie->Regex-Muster, "*" fuer den globalen Default (7.5), '
        'z. B. \'{"*": "^\\\\d+_+"}\' entfernt numerische Praefixe vor dem Namensabgleich.',
    ),
) -> None:
    client = get_client()
    body: dict = {"compare": json.loads(compare.read_text(encoding="utf-8"))}
    if base:
        body["base"] = json.loads(base.read_text(encoding="utf-8"))
    if category:
        body["categories"] = category
    if ignore_regex:
        body["ignore_regex"] = json.loads(ignore_regex)
    result = client.post("config-service", "config/compare", json_body=body)
    if output_format(ctx) == "json":
        emit(result, output_format="json")
        return
    rows = [
        {
            "category": name,
            "only_in_base": len(delta["only_in_base"]),
            "only_in_compare": len(delta["only_in_compare"]),
            "differing": len(delta["differing"]),
            "identical": len(delta["identical"]),
        }
        for name, delta in result["categories"].items()
    ]
    emit(
        rows,
        output_format="table",
        columns=["category", "only_in_base", "only_in_compare", "differing", "identical"],
    )
    typer.echo("Detailanzeige je abweichendem Attribut: --output json")


@config_app.command("import")
def import_config(
    ctx: typer.Context,
    file: Path = typer.Argument(
        ...,
        exists=True,
        help="Konfigurationspaket bzw. Export (JSON), z. B. packages/egov/config.json.",
    ),
    category: list[str] = typer.Option(
        [],
        "--category",
        help="Wiederholbar - nur diese Kategorien anwenden. Default: alle im Paket.",
    ),
) -> None:
    """Wendet ein Konfigurationspaket an (7.3, P96-S2). Vorher mit
    'config compare <paket> ' pruefen, was sich aendert. Ist fuer config.import das
    Vier-Augen-Prinzip aktiv, entsteht ein Freigabeantrag statt einer Aenderung."""
    client = get_client()
    document = json.loads(file.read_text(encoding="utf-8"))
    result = client.post(
        "config-service",
        "config/import",
        json_body=document,
        params={"categories": category} if category else None,
    )
    if output_format(ctx) == "json":
        emit(result, output_format="json")
        return
    if result.get("status") == "pending_approval":
        typer.echo(
            "Vier-Augen-Prinzip aktiv: Freigabeantrag "
            f"{result.get('approval_request_id')} angelegt - nichts geaendert, bis eine "
            "zweite Person ihn freigibt ('dms approvals show <id>')."
        )
        return
    rows = [
        {
            "category": name,
            "created": entry.get("created", 0),
            "updated": entry.get("updated", 0),
            "skipped": entry.get("skipped", 0),
            "errors": len(entry.get("errors") or []),
        }
        for name, entry in ((result.get("result") or {}).get("results") or {}).items()
    ]
    emit(
        rows, output_format="table", columns=["category", "created", "updated", "skipped", "errors"]
    )
    errors = [
        f"{name}: {message}"
        for name, entry in ((result.get("result") or {}).get("results") or {}).items()
        for message in entry.get("errors") or []
    ]
    for line in errors:
        typer.echo(f"Fehler - {line}", err=True)
    if errors:
        raise typer.Exit(code=1)
