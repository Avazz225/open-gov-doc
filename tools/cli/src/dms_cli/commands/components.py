"""Software components (P100-S1, ADR 0271): which libraries run where - per
service from the running instances and the frontends' files, a search across
services, and a CycloneDX export for external scanners. monitoring-service,
`domain-admin-monitoring`."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit, print_record

components_app = typer.Typer(
    help="Eingesetzte Bibliotheken je Dienst, Suche über alle Dienste, CycloneDX-Export."
)


def register(app: typer.Typer) -> None:
    app.add_typer(components_app, name="components")


@components_app.command("list")
def list_cmd(
    ctx: typer.Context,
    refresh: bool = typer.Option(False, "--refresh", help="Nicht den 60-s-Zwischenstand nehmen."),
) -> None:
    """Je Dienst: Instanzen, Anzahl je Ökosystem, Replikas gleich, Abgleich mit dem Image."""
    rows = get_client().get("monitoring-service", "components", params={"refresh": refresh})
    if output_format(ctx) == "json":
        emit(rows, output_format="json")
        return
    emit(
        [
            {
                "dienst": r["service"],
                "art": r["kind"],
                "instanzen": r["instances"],
                "komponenten": r["component_count"],
                "ökosysteme": ", ".join(f"{k} {v}" for k, v in r["ecosystems"].items()),
                "replikas gleich": "ja" if r["replicas_consistent"] else "NEIN",
                "wie im image": {True: "ja", False: "NEIN", None: "–"}[r["matches_image"]],
                "hinweis": r["error"] or "",
            }
            for r in rows
        ],
        output_format="table",
    )


@components_app.command("show")
def show_cmd(
    ctx: typer.Context,
    service: str = typer.Argument(..., help="Dienst oder Frontend, z. B. document-service."),
    filter_text: str | None = typer.Option(None, "--filter", help="Nur Namen, die das enthalten."),
) -> None:
    """Alle Komponenten eines Dienstes."""
    detail = get_client().get("monitoring-service", f"components/{service}")
    rows = [
        c
        for c in detail["components"]
        if not filter_text or filter_text.lower() in c["name"].lower()
    ]
    if output_format(ctx) == "json":
        emit({**detail, "components": rows}, output_format="json")
        return
    image = detail.get("image_file") or {}
    print_record(
        {
            "dienst": detail["service"],
            "instanzen": detail["instances"],
            "komponenten": detail["component_count"],
            "image erstellt": image.get("created") or "–",
            "wie im image": {True: "ja", False: "NEIN", None: "–"}[detail["matches_image"]],
        }
    )
    typer.echo("")
    emit(rows, output_format="table", columns=["name", "version", "ecosystem", "own"])


@components_app.command("search")
def search_cmd(
    ctx: typer.Context, query: str = typer.Argument(..., help="Paketname (Teil genügt).")
) -> None:
    """Welche Dienste ein Paket einsetzen, in welcher Version."""
    rows = get_client().get("monitoring-service", "components/search", params={"q": query})
    emit(
        rows, output_format=output_format(ctx), columns=["name", "version", "ecosystem", "service"]
    )


@components_app.command("export")
def export_cmd(
    service: str | None = typer.Option(None, "--service", help="Nur dieser Dienst."),
    out: Path = typer.Option(..., "-o", "--out", help="Zieldatei (CycloneDX JSON)."),
) -> None:
    """CycloneDX 1.6 für die ganze Installation (jede Komponente einmal, mit den
    Diensten, die sie nutzen) oder einen Dienst - für Dependency-Track, Trivy & Co."""
    document = get_client().get(
        "monitoring-service", "components/export", params={"service": service}
    )
    out.write_text(json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"{out}: {len(document.get('components', []))} Komponenten")
