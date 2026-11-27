"""Test-only helper that builds signed licence JWTs (ADR 0032). Signs with the
throwaway key `conftest.py` generates per test session (P98-S2), never with
the licensor's key. The issuing tool is `tools/license-tool` (ADR 0266)."""

import os
from datetime import UTC, datetime, timedelta

from jose import jwt

# Set by conftest.py at import time, before any test module imports this.
_PRIVATE_KEY_PEM = os.environ["TEST_LICENSE_PRIVATE_KEY_PEM"]


def make_license_token(
    *,
    expires_in_days: int = 365,
    issued_days_ago: int = 0,
    user_model: str = "concurrent",
    max_users: int | None = 10,
    storage_limit_gb: float | None = 100.0,
    document_limit: int | None = 1000,
    licensed_components: list[str] | None = None,
    installation_id: str | None = None,
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
    # installation_id (3a, P13-S1) ist ein optionales Claim - standardmäßig
    # weggelassen, damit bestehende Tests unveränderte, "ungebundene"
    # Lizenzen weiter simulieren können (Rückwärtskompatibilität).
    if installation_id is not None:
        claims["installation_id"] = installation_id
    return jwt.encode(claims, _PRIVATE_KEY_PEM, algorithm="RS256")
