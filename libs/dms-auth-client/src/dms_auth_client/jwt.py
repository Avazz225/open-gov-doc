import time

import httpx
from jose import jwt
from jose.exceptions import JWTError


class InvalidTokenError(Exception):
    pass


class TokenValidator:
    """Stateless JWT validation against a JWKS (4.4) - no round trip to Keycloak
    during normal operation, just a cached JWKS refresh.

    For tests, ``jwks`` can be passed directly instead of ``jwks_url``, without
    needing a real Keycloak instance.
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
    """SSO/auth decoupling (post-roadmap feature, phase 18) - delegates to one of
    several `TokenValidator` instances, selected via the `iss` claim of the
    respective token. Reason: since phase 18, `auth-service` can issue tokens
    for technical accounts (superusers/domain admins) itself, in addition to
    Keycloak (own issuer, own JWKS) - downstream consumers
    (`make_current_user_dependency`) should not need to know about this, hence
    the same `.validate(token) -> dict` interface as `TokenValidator`
    itself (pure duck typing, no shared base type needed)."""

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
