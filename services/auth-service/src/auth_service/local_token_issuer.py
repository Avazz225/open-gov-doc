import secrets
import time
from datetime import UTC, datetime

import bcrypt
from jose import jwk as jose_jwk
from jose import jwt

from auth_service import federation_crypto
from auth_service.models import LocalSigningKey

# Auth-Entkopplung von Keycloak (Post-Roadmap-Feature, Phase 18, ADR 0063) -
# kein URL-Issuer (anders als Keycloaks `{base_url}/realms/{realm}`), da
# dieser Issuer keinen eigenen HTTP-Endpunkt außer dem lokalen JWKS braucht
# und absichtlich nicht mit einer erreichbaren Adresse verwechselt werden
# soll - `MultiIssuerTokenValidator` wählt allein über den `iss`-String,
# keine Netzwerkoperation involviert.
LOCAL_ISSUER = "dms-auth-service-local"
_SIGNING_KEY_ID = 1
_DEFAULT_KID = "local-1"


async def ensure_signing_key(session_factory) -> LocalSigningKey:
    """Idempotent (gleiches Muster wie `main._ensure_federation_identity`) -
    erzeugt beim allerersten Aufruf ein neues RSA-Schlüsselpaar und
    persistiert es, jeder weitere Aufruf liest nur die bereits vorhandene
    Zeile. Nutzt `federation_crypto.generate_keypair()` wieder (RSA-2048,
    PEM/PKCS8) - selber Service, keine neue Duplikation."""
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
    """Liefert dasselbe JWKS-Format wie Keycloaks `/protocol/openid-connect/
    certs` - `dms_auth_client.TokenValidator` unterscheidet nicht, woher ein
    JWKS stammt."""
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
    """Stellt ein einzelnes Token mit demselben Claim-Shape wie Keycloak aus
    (`sub`, `preferred_username`, `realm_access.roles`, `aud`) - Downstream-
    Konsumenten (`GET /me`, `permission-service`-Aufrufe, die `sub`/Rollen
    auslesen) müssen dadurch nichts über die Herkunft des Tokens wissen.
    Liefert nur den rohen Token-String zurück; das Zusammensetzen der vollen
    `TokenResponse` (Access- + Refresh-Token, je ein Aufruf mit
    unterschiedlicher Gültigkeitsdauer) ist Sache des Aufrufers (`POST
    /login`, Phase 18 Session 2) - kein echtes Keycloak-Refresh-Grant, da
    rein lokale Konten keine Keycloak-Session besitzen, gegen die refresht
    werden könnte. Trägt einen `jti` (registrierter JWT-Claim für genau
    diesen Zweck) - ohne ihn wären zwei innerhalb derselben Sekunde
    ausgestellte Tokens für dasselbe Konto (z. B. Login direkt gefolgt von
    Refresh) byte-identisch, da `iat`/`exp` nur Sekundenauflösung haben und
    alle übrigen Claims sich nicht unterscheiden - real bei der
    Testentwicklung dieser Session aufgetreten."""
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
    """Peekt den `iss`-Claim OHNE Signaturprüfung, um `POST /refresh` zu
    entscheiden, ob ein Token lokal (`local_token_issuer`) oder von Keycloak
    ausgestellt wurde - reine Weichenstellung, welcher der beiden Refresh-
    Pfade zuständig ist; die eigentliche Signaturprüfung passiert erst
    danach (`MultiIssuerTokenValidator`/Keycloaks Token-Endpunkt). Liefert
    `False` bei jedem unparsbaren Token (fällt dann auf den bisherigen
    Keycloak-Pfad zurück, dessen Fehlerverhalten für kaputte Tokens bereits
    etabliert ist)."""
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
