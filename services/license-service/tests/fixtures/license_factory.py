"""Test-only Hilfsfunktion, um signierte Lizenz-JWTs zu bauen - der private
Schluessel hier ist ein Wegwerf-Entwicklungsschluessel (siehe
dev_private_key.pem), niemals der echte Lizenzgeber-Schluessel (ADR 0032).
Kein Bestandteil eines Lizenzausstellungswerkzeugs, nur fuer Tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from jose import jwt

_PRIVATE_KEY_PEM = (Path(__file__).parent / "dev_private_key.pem").read_text(encoding="utf-8")


def make_license_token(
    *,
    expires_in_days: int = 365,
    issued_days_ago: int = 0,
    user_model: str = "concurrent",
    max_users: int | None = 10,
    storage_limit_gb: float | None = 100.0,
    document_limit: int | None = 1000,
    licensed_components: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "iat": now - timedelta(days=issued_days_ago),
        "exp": now + timedelta(days=expires_in_days),
        "user_model": user_model,
        "max_users": max_users,
        "storage_limit_gb": storage_limit_gb,
        "document_limit": document_limit,
        "licensed_components": licensed_components,
    }
    return jwt.encode(claims, _PRIVATE_KEY_PEM, algorithm="RS256")
