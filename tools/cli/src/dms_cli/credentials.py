"""Credential-Speicherung fuer das CLI (6.2) - eigene Datei statt eines
Systemschluesselbunds, um ohne Zusatzabhaengigkeiten auf jeder Plattform
gleich zu funktionieren. `DMS_GATEWAY_URL`/`DMS_TOKEN` ueberschreiben die
Datei immer, unabhaengig davon ob eine existiert - das ist der CI/CD-Pfad,
solange auth-service keinen echten Service-Account-Grant anbietet (siehe
docs/tools/cli.md "Offene Punkte")."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

DEFAULT_GATEWAY_URL = "http://localhost:8009"


@dataclass
class Credentials:
    gateway_url: str
    access_token: str | None = None
    refresh_token: str | None = None
    username: str | None = None


def _credentials_dir() -> Path:
    return Path(os.environ.get("DMS_CLI_HOME", str(Path.home() / ".dms")))


def credentials_path() -> Path:
    return _credentials_dir() / "credentials.json"


def load_credentials() -> Credentials | None:
    env_token = os.environ.get("DMS_TOKEN")
    if env_token:
        return Credentials(
            gateway_url=os.environ.get("DMS_GATEWAY_URL", DEFAULT_GATEWAY_URL),
            access_token=env_token,
            refresh_token=None,
            username=None,
        )

    path = credentials_path()
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    gateway_url = os.environ.get("DMS_GATEWAY_URL", data.get("gateway_url", DEFAULT_GATEWAY_URL))
    return Credentials(
        gateway_url=gateway_url,
        access_token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        username=data.get("username"),
    )


def save_credentials(creds: Credentials) -> None:
    directory = _credentials_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)
    path = credentials_path()
    path.write_text(
        json.dumps(
            {
                "gateway_url": creds.gateway_url,
                "access_token": creds.access_token,
                "refresh_token": creds.refresh_token,
                "username": creds.username,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def clear_credentials() -> None:
    path = credentials_path()
    if path.is_file():
        path.unlink()
