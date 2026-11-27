"""Vulnerability checks (P100-S2, ADR 0272): is this installation affected by a
CVE? Online the id is looked up live (OSV.dev, if the installation allows it);
air-gapped, a record file or a hand-entered package with its affected range.
OG Doc ships no vulnerability data. monitoring-service, `domain-admin-monitoring`."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

vulnerabilities_app = typer.Typer(
    help="Prüfen, ob die Installation von einer Schwachstelle betroffen ist."
)

_ECOSYSTEMS = ("PyPI", "npm", "Debian", "Alpine")


def register(app: typer.Typer) -> None:
    app.add_typer(vulnerabilities_app, name="vulnerabilities")


@vulnerabilities_app.command("check")
def check_cmd(
    ctx: typer.Context,
    vuln_id: str | None = typer.Argument(None, help="CVE-…/GHSA-… (online)."),
    file: Path | None = typer.Option(None, "--file", help="OSV-Datensatz als JSON (air-gapped)."),
    package: str | None = typer.Option(None, "--package", help="Paket von Hand (air-gapped)."),
    ecosystem: str | None = typer.Option(None, "--ecosystem", help=", ".join(_ECOSYSTEMS)),
    introduced: str | None = typer.Option(
        None, "--introduced", help="Betroffen ab (Standard: alle)."
    ),
    fixed: str | None = typer.Option(None, "--fixed", help="Behoben in."),
    last_affected: str | None = typer.Option(None, "--last-affected"),
) -> None:
    """Genau eines: eine Kennung, `--file` oder `--package` mit `--ecosystem`."""
    given = sum(x is not None for x in (vuln_id, file, package))
    if given != 1:
        raise typer.BadParameter("Genau eines angeben: Kennung, --file oder --package")
    if vuln_id:
        body: dict = {"id": vuln_id}
    elif file:
        body = {"record": json.loads(file.read_text(encoding="utf-8"))}
    else:
        if ecosystem not in _ECOSYSTEMS:
            raise typer.BadParameter(f"--ecosystem: eines von {', '.join(_ECOSYSTEMS)}")
        body = {
            "manual": {
                "ecosystem": ecosystem,
                "package": package,
                "introduced": introduced,
                "fixed": fixed,
                "last_affected": last_affected,
            }
        }
    result = get_client().post("monitoring-service", "vulnerabilities/check", json_body=body)
    if output_format(ctx) == "json":
        emit(result, output_format="json")
        return
    for record in result["records"]:
        severity = f" [{record['severity']}]" if record.get("severity") else ""
        # Distribution records carry multi-line prose; one short line each.
        summary = " ".join((record.get("summary") or "").split())
        if len(summary) > 140:
            summary = summary[:139] + "…"
        typer.echo(f"{record['id']}{severity}: {summary}".strip())
    typer.echo(
        f"Quelle: {result['source']} · geprüft: {result['checked_services']} Dienste/Oberflächen"
    )
    typer.echo("")
    if result["affected"]:
        typer.secho(f"BETROFFEN ({len(result['affected'])}):", fg=typer.colors.RED)
        emit(
            result["affected"],
            output_format="table",
            columns=["service", "component", "version", "fixed_in", "ecosystem", "record_id"],
        )
    else:
        typer.secho("Nicht betroffen.", fg=typer.colors.GREEN)
    if result["not_affected"]:
        typer.echo("")
        typer.echo(
            f"Installiert, aber nicht im betroffenen Bereich ({len(result['not_affected'])}):"
        )
        emit(
            result["not_affected"],
            output_format="table",
            columns=["service", "component", "version", "ecosystem", "note"],
        )
