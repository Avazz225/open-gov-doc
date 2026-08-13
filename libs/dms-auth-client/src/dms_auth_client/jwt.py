import time

import httpx
from jose import jwt
from jose.exceptions import JWTError


class InvalidTokenError(Exception):
    pass


class TokenValidator:
    """Zustandslose JWT-Prüfung gegen ein JWKS (4.4) - keine Rückfrage bei Keycloak
    im laufenden Betrieb, nur eine gecachte JWKS-Aktualisierung.

    Für Tests kann ``jwks`` statt ``jwks_url`` direkt übergeben werden, ohne einen
    echten Keycloak zu benötigen.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        jwks: dict | None = None,
        cache_ttl: float = 300,
    ) -> None:
        if jwks_url is None and jwks is None:
            raise ValueError("jwks_url oder jwks muss angegeben werden")
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._jwks = jwks
        self._cache_ttl = cache_ttl
        self._fetched_at = time.monotonic() if jwks is not None else 0.0

    def _get_jwks(self) -> dict:
        age = time.monotonic() - self._fetched_at
        cache_valid = self._jwks is not None and age < self._cache_ttl
        if cache_valid:
            return self._jwks  # type: ignore[return-value]
        if self._jwks_url is None:
            if self._jwks is not None:
                return self._jwks
            raise InvalidTokenError("Kein JWKS verfügbar")
        response = httpx.get(self._jwks_url, timeout=5.0)
        response.raise_for_status()
        self._jwks = response.json()
        self._fetched_at = time.monotonic()
        return self._jwks

    @property
    def issuer(self) -> str:
        return self._issuer

    def validate(self, token: str) -> dict:
        jwks = self._get_jwks()
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise InvalidTokenError("Ungültiger Token-Header") from exc

        key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
        if key is None:
            raise InvalidTokenError(f"Kein passender Schlüssel für kid={header.get('kid')}")

        try:
            return jwt.decode(
                token,
                key,
                algorithms=[key.get("alg", "RS256")],
                audience=self._audience,
                issuer=self._issuer,
            )
        except JWTError as exc:
            raise InvalidTokenError("Token-Validierung fehlgeschlagen") from exc


class MultiIssuerTokenValidator:
    """SSO/Auth-Entkopplung (Post-Roadmap-Feature, Phase 18) - delegiert an eine
    von mehreren `TokenValidator`-Instanzen, ausgewählt über den `iss`-Claim
    des jeweiligen Tokens. Grund: `auth-service` kann seit Phase 18 zusätzlich
    zu Keycloak selbst Tokens für technische Konten (Superuser/Domain-Admins)
    ausstellen (eigener Issuer, eigenes JWKS) - Downstream-Konsumenten
    (`make_current_user_dependency`) sollen davon nichts wissen müssen, daher
    dieselbe `.validate(token) -> dict`-Schnittstelle wie `TokenValidator`
    selbst (reines Duck-Typing, kein gemeinsamer Basistyp nötig)."""

    def __init__(self, validators: list[TokenValidator]) -> None:
        if not validators:
            raise ValueError("Mindestens ein TokenValidator muss angegeben werden")
        self._by_issuer = {v.issuer: v for v in validators}

    def validate(self, token: str) -> dict:
        try:
            claims = jwt.get_unverified_claims(token)
        except JWTError as exc:
            raise InvalidTokenError("Ungültiger Token-Payload") from exc

        issuer = claims.get("iss")
        validator = self._by_issuer.get(issuer)
        if validator is None:
            raise InvalidTokenError(f"Unbekannter Issuer: {issuer!r}")
        return validator.validate(token)
