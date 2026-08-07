"""Ausgabeformatierung - Default eine abhaengigkeitsfreie Tabelle fuer den
interaktiven Gebrauch, `--output json` fuer Skripte/Pipelines (Konzept 6.2:
"scriptfaehige Ausgabeformate wie JSON")."""

from __future__ import annotations

import json
from typing import Any

import typer


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def print_table(rows: list[dict], columns: list[str] | None = None) -> None:
    if not rows:
        typer.echo("Keine Ergebnisse.")
        return
    cols = columns or list(rows[0].keys())
    cells = [[_cell(row.get(c)) for c in cols] for row in rows]
    widths = [
        max(len(cols[i]), max((len(row[i]) for row in cells), default=0)) for i in range(len(cols))
    ]
    header = "  ".join(cols[i].ljust(widths[i]) for i in range(len(cols)))
    typer.echo(header)
    typer.echo("  ".join("-" * widths[i] for i in range(len(cols))))
    for row in cells:
        typer.echo("  ".join(row[i].ljust(widths[i]) for i in range(len(cols))))


def print_record(record: dict) -> None:
    width = max((len(k) for k in record), default=0)
    for key, value in record.items():
        typer.echo(f"{key.ljust(width)} : {_cell(value)}")


def emit(data: Any, *, output_format: str, columns: list[str] | None = None) -> None:
    if output_format == "json":
        print_json(data)
        return
    if isinstance(data, list):
        print_table(data, columns)
    elif isinstance(data, dict):
        print_record(data)
    else:
        typer.echo(_cell(data))
