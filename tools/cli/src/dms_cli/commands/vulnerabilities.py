"""Vulnerability checks (P100-S2/S3, ADR 0272): is this installation affected by
a CVE, and which known vulnerabilities affect it at all? Online, records come
live from OSV.dev (if the installation allows it); air-gapped, from a record
file, a hand-entered range, or an imported OSV database. OG Doc ships no
vulnerability data. monitoring-service, `domain-admin-monitoring`."""

from __future__ import annotations

import json
import re
import time
import zipfile
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
    if result.get("note"):
        typer.secho(result["note"], fg=typer.colors.YELLOW)
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


# --- Scanning the whole inventory (P100-S3) ---------------------------------------

_SEVERITY_ORDER = ("critical", "high", "medium", "low", "unknown")


def _print_report(scan: dict, *, severities: set[str] | None, fixable_only: bool) -> None:
    summary = scan.get("summary") or {}
    typer.echo(
        f"Prüfung {scan['id']} · {scan['source']} · {scan['trigger']} · "
        f"{scan.get('finished_at') or scan['started_at']} · {scan['checked_services']} Dienste"
    )
    if scan["status"] != "done":
        typer.secho(f"Status: {scan['status']} {scan.get('error') or ''}".strip(), fg="red")
        return
    by_severity = summary.get("by_severity") or {}
    counts = ", ".join(
        f"{level} {sum((by_severity.get(level) or {}).values())}" for level in _SEVERITY_ORDER
    )
    typer.echo(
        f"{summary.get('vulnerabilities', 0)} bekannte Schwachstellen "
        f"({summary.get('fixable', 0)} behebbar): {counts}"
    )
    rows = []
    for entry in scan.get("vulnerabilities") or []:
        if severities and entry["severity"] not in severities:
            continue
        if fixable_only and not entry["fixable"]:
            continue
        fixes = sorted({i["fixed_in"] for i in entry["installations"] if i["fixed_in"]})
        services = sorted({i["service"] for i in entry["installations"]})
        packages = sorted({f"{i['component']} {i['version']}" for i in entry["installations"]})
        summary_text = " ".join((entry.get("summary") or "").split())
        rows.append(
            {
                "id": entry["id"],
                "severity": entry["severity"],
                "score": entry.get("score") or "",
                "fix": ", ".join(fixes) or "–",
                "packages": ", ".join(packages)[:60],
                "services": len(services),
                "summary": summary_text[:70] + ("…" if len(summary_text) > 70 else ""),
            }
        )
    if rows:
        typer.echo("")
        emit(
            rows,
            output_format="table",
            columns=["id", "severity", "score", "fix", "packages", "services", "summary"],
        )
    uncovered = scan.get("uncovered") or []
    if uncovered:
        typer.echo("")
        typer.echo(f"Nicht geprüft ({len(uncovered)} Pakete):")
        reasons: dict[str, int] = {}
        for row in uncovered:
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
        for reason, count in sorted(reasons.items()):
            typer.echo(f"  {reason}: {count}")


@vulnerabilities_app.command("scan")
def scan_cmd(
    ctx: typer.Context,
    source: str = typer.Option("auto", "--source", help="auto, online oder offline."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Auf das Ergebnis warten."),
    timeout: int = typer.Option(900, "--timeout", help="Sekunden, die höchstens gewartet wird."),
) -> None:
    """Alle Komponenten auf bekannte Schwachstellen prüfen (online per OSV-Batch, sofern
    erlaubt, sonst gegen die eingespielte Datenbank)."""
    if source not in ("auto", "online", "offline"):
        raise typer.BadParameter("--source: auto, online oder offline")
    client = get_client()
    started = client.post(
        "monitoring-service", "vulnerabilities/scan", json_body={"source": source}
    )
    if not wait:
        emit(started, output_format=output_format(ctx))
        return
    deadline = time.monotonic() + timeout
    scan = started
    while time.monotonic() < deadline:
        scan = client.get("monitoring-service", f"vulnerabilities/scans/{started['id']}")
        if scan["status"] != "running":
            break
        time.sleep(2)
    if output_format(ctx) == "json":
        emit(scan, output_format="json")
        return
    if scan["status"] == "running":
        typer.echo(f"Läuft noch: dms vulnerabilities report --scan {started['id']}")
        return
    _print_report(scan, severities=None, fixable_only=False)
    if scan["status"] == "failed":
        raise typer.Exit(1)


@vulnerabilities_app.command("report")
def report_cmd(
    ctx: typer.Context,
    scan_id: str | None = typer.Option(None, "--scan", help="Eine bestimmte Prüfung."),
    severity: str | None = typer.Option(None, "--severity", help="z. B. critical,high"),
    fixable: bool = typer.Option(False, "--fixable", help="Nur behebbare."),
) -> None:
    """Das Ergebnis der letzten (oder einer bestimmten) Prüfung."""
    path = f"vulnerabilities/scans/{scan_id}" if scan_id else "vulnerabilities/scans/latest"
    scan = get_client().get("monitoring-service", path)
    if output_format(ctx) == "json":
        emit(scan, output_format="json")
        return
    wanted = {s.strip().lower() for s in severity.split(",")} if severity else None
    _print_report(scan, severities=wanted, fixable_only=fixable)


@vulnerabilities_app.command("scans")
def scans_cmd(ctx: typer.Context, limit: int = typer.Option(10, "--limit")) -> None:
    """Die letzten Prüfungen."""
    rows = get_client().get("monitoring-service", "vulnerabilities/scans", params={"limit": limit})
    for row in rows:
        summary = row.get("summary") or {}
        row["vulnerabilities"] = summary.get("vulnerabilities", "")
        row["fixable"] = summary.get("fixable", "")
    emit(
        rows,
        output_format=output_format(ctx),
        columns=["id", "status", "source", "trigger", "started_at", "vulnerabilities", "fixable"],
    )


# --- Offline database --------------------------------------------------------------

database_app = typer.Typer(help="OSV-Datenbank für air-gapped Installationen.")
vulnerabilities_app.add_typer(database_app, name="database")


def _normalise(ecosystem: str, name: str) -> str:
    """Same rule as monitoring-service (PEP 503 for PyPI)."""
    if ecosystem.split(":", 1)[0] == "PyPI":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name


def filter_osv_dump(path: Path, wanted: dict[str, set[str]]) -> tuple[list[dict], dict[str, int]]:
    """Reads an OSV `all.zip` (or a JSON record list) and keeps the records that
    name an installed package. Also counts the records per ecosystem in the
    whole file: monitoring-service decides from those counts which ecosystems
    the file covers (a PyPI dump also holds a few records spanning npm). The
    server filters again; this only keeps the upload small (npm: over 200 MB)."""
    kept: list[dict] = []
    seen: dict[str, int] = {}

    def consider(record: dict) -> None:
        if not isinstance(record, dict) or not record.get("id"):
            return
        relevant = False
        ecosystems = {
            (entry.get("package") or {}).get("ecosystem") or ""
            for entry in record.get("affected") or []
        }
        for eco in ecosystems - {""}:
            seen[eco] = seen.get(eco, 0) + 1
        for entry in record.get("affected") or []:
            package = entry.get("package") or {}
            eco = package.get("ecosystem") or ""
            if eco not in wanted:
                continue
            if _normalise(eco, package.get("name") or "") in wanted[eco]:
                relevant = True
        if relevant:
            kept.append(record)

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.filename.endswith(".json"):
                    try:
                        consider(json.loads(archive.read(info)))
                    except (ValueError, UnicodeDecodeError):
                        continue
    else:
        body = json.loads(path.read_text(encoding="utf-8"))
        for record in body if isinstance(body, list) else body.get("records") or [body]:
            consider(record)
    return kept, seen


@database_app.command("import")
def database_import_cmd(
    files: list[Path] = typer.Argument(..., help="OSV-Dumps (all.zip je Ökosystem) oder JSON."),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
) -> None:
    """OSV-Datenbank einspielen, z. B. PyPI/all.zip, npm/all.zip, Debian/all.zip,
    Alpine/all.zip von osv-vulnerabilities.storage.googleapis.com. Behalten wird nur,
    was die Installation heute einsetzt."""
    client = get_client()
    wanted = {
        eco: set(names)
        for eco, names in client.get(
            "monitoring-service", "vulnerabilities/database/wanted"
        ).items()
    }
    for path in files:
        records, counts = filter_osv_dump(path, wanted)
        seen = {eco for eco in counts if eco in wanted}
        if not seen:
            typer.secho(
                f"{path.name}: keine Datensätze für {', '.join(sorted(wanted))} - übersprungen",
                fg="yellow",
            )
            continue
        typer.echo(f"{path.name}: {len(records)} relevante Datensätze")
        job = client.post(
            "monitoring-service",
            "vulnerabilities/database",
            json_body={"records": records, "ecosystems": counts},
            params={"source_name": path.name},
        )
        if not wait:
            continue
        for _ in range(300):
            jobs = client.get("monitoring-service", "vulnerabilities/config")["imports"]
            current = next((j for j in jobs if j["id"] == job["id"]), None)
            if current and current["status"] != "running":
                break
            time.sleep(1)
        if current and current["status"] == "done":
            typer.secho(f"  eingespielt: {current['ecosystems']}", fg="green")
        else:
            typer.secho(f"  fehlgeschlagen: {(current or {}).get('error')}", fg="red")
            raise typer.Exit(1)


@database_app.command("status")
def database_status_cmd(ctx: typer.Context) -> None:
    """Eingespielte Datenbanken und die letzten Importe."""
    config = get_client().get("monitoring-service", "vulnerabilities/config")
    if output_format(ctx) == "json":
        emit(config, output_format="json")
        return
    typer.echo(
        f"Online-Einzelabfrage: {'an' if config['online'] else 'aus'} · "
        f"Online-Gesamtprüfung: {'an' if config['scan_online'] else 'aus'} · "
        f"Intervall: {config['scan_interval_hours']} h"
    )
    if config["databases"]:
        emit(
            config["databases"],
            output_format="table",
            columns=[
                "ecosystem",
                "record_count",
                "covered_count",
                "data_as_of",
                "imported_at",
                "source_name",
            ],
        )
    else:
        typer.echo("Keine Datenbank eingespielt.")


@database_app.command("delete")
def database_delete_cmd(ecosystem: str = typer.Argument(..., help="z. B. PyPI, Debian:13")) -> None:
    get_client().delete("monitoring-service", f"vulnerabilities/database/{ecosystem}")
    typer.echo(f"Datenbank {ecosystem} entfernt.")
