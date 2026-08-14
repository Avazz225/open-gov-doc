import secrets
import time
from datetime import UTC, datetime

import bcrypt
from jose import jwk as jose_jwk
from jose import jwt

from auth_service import federation_crypto
from auth_service.models import LocalSigningKey

# Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR 0063) -
# not a URL issuer (unlike Keycloak's `{base_url}/realms/{realm}`), since
# this issuer needs no HTTP endpoint of its own besides the local JWKS and
# is deliberately meant not to be mistaken for a reachable address -
# `MultiIssuerTokenValidator` selects purely via the `iss` string, no
# network operation involved.
LOCAL_ISSUER = "dms-auth-service-local"
_SIGNING_KEY_ID = 1
_DEFAULT_KID = "local-1"


async def ensure_signing_key(session_factory) -> LocalSigningKey:
    """Idempotent (same pattern as `main._ensure_federation_identity`) -
    generates a new RSA key pair and persists it on the very first call,
    every subsequent call just reads the already-existing row. Reuses
    `federation_crypto.generate_keypair()` (RSA-2048, PEM/PKCS8) - same
    service, no new duplication."""
    async with session_factory() as session:
        key = await session.get(LocalSigningKey, _SIGNING_KEY_ID)
        if key is None:
            private_pem, public_pem = federation_crypto.generate_keypair()
            key = LocalSigningKey(
                id=_SIGNING_KEY_ID,
                kid=_DEFAULT_KID,
                private_key_pem=private_pem,
                public_key_pem=public_pem,
                created_at=datetime.now(UTC),
            )
            session.add(key)
            await session.commit()
            await session.refresh(key)
        return key


def build_jwks(public_key_pem: bytes, kid: str) -> dict:
    """Returns the same JWKS format as Keycloak's `/protocol/openid-connect/
    certs` - `dms_auth_client.TokenValidator` doesn't distinguish where a
    JWKS came from."""
    public_jwk = jose_jwk.construct(public_key_pem.decode("ascii"), algorithm="RS256").to_dict()
    public_jwk["kid"] = kid
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return {"keys": [public_jwk]}


def mint_token(
    *,
    private_key_pem: bytes,
    kid: str,
    audience: str,
    subject: str,
    username: str,
    roles: list[str],
    expires_in_seconds: int,
) -> str:
    """Issues a single token with the same claim shape as Keycloak
    (`sub`, `preferred_username`, `realm_access.roles`, `aud`) - downstream
    consumers (`GET /me`, `permission-service` calls that read `sub`/roles)
    therefore don't need to know anything about the token's origin.
    Returns only the raw token string; assembling the full `TokenResponse`
    (access + refresh token, one call each with a different validity
    duration) is the caller's job (`POST /login`, Phase 18 Session 2) - no
    real Keycloak refresh grant, since purely local accounts have no
    Keycloak session to refresh against. Carries a `jti` (registered JWT
    claim for exactly this purpose) - without it, two tokens issued within
    the same second for the same account (e.g. login immediately followed
    by refresh) would be byte-identical, since `iat`/`exp` only have
    second-level resolution and all other claims don't differ - this
    actually occurred during test development in this session."""
    now = int(time.time())
    claims = {
        "iss": LOCAL_ISSUER,
        "aud": audience,
        "sub": subject,
        "preferred_username": username,
        "realm_access": {"roles": roles},
        "iat": now,
        "exp": now + expires_in_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(
        claims, private_key_pem.decode("ascii"), algorithm="RS256", headers={"kid": kid}
    )


def is_local_token(token: str) -> bool:
    """Peeks at the `iss` claim WITHOUT signature verification, to let
    `POST /refresh` decide whether a token was issued locally
    (`local_token_issuer`) or by Keycloak - purely a routing decision for
    which of the two refresh paths is responsible; the actual signature
    verification only happens afterward (`MultiIssuerTokenValidator`/
    Keycloak's token endpoint). Returns `False` for any unparsable token
    (then falls back to the existing Keycloak path, whose error behavior
    for broken tokens is already established)."""
    try:
        claims = jwt.get_unverified_claims(token)
    except Exception:
        return False
    return claims.get("iss") == LOCAL_ISSUER


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False
