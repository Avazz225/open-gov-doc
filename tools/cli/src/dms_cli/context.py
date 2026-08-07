"""Gemeinsame Helfer fuer Command-Module: Client-Aufbau aus gespeicherten
Zugangsdaten, Zugriff auf das globale --output/-o (main.py-Callback setzt
ctx.obj)."""

from __future__ import annotations

import os

import typer

from dms_cli import credentials
from dms_cli.client import GatewayClient


def get_client() -> GatewayClient:
    creds = credentials.load_credentials()
    if creds is None or not creds.access_token:
        typer.echo("Nicht angemeldet - bitte 'dms login' ausfuehren.", err=True)
        raise typer.Exit(code=1)
    # Env-Var-Token (CI/CD-Pfad) wird nie in die Datei zurueckgeschrieben -
    # ein Refresh dort bleibt ausschliesslich im Prozessspeicher.
    persist = "DMS_TOKEN" not in os.environ
    on_refresh = credentials.save_credentials if persist else None
    return GatewayClient(creds, on_refresh=on_refresh)


def output_format(ctx: typer.Context) -> str:
    return (ctx.obj or {}).get("output", "table")
