"""Licence tokens: the claims license-service reads, signing, and the same
checks the service applies (`license_verifier.decode` + `usage.compute_usage`).

Format (ADR 0032): a JWT signed with RS256. Claims the service evaluates:
- `iat`, `exp`, optional `nbf` (epoch seconds);
- `user_model` (`named` | `concurrent`), `max_users`;
- `storage_limit_gb`, `document_limit` (`null` = unlimited);
- `licensed_components` (`null` = every licensable component);
- optional `installation_id` (binds the licence to one installation).

The tool adds `jti` (licence id), `sub` (licensee) and `iss`; the service
ignores them, they serve the licensor's records and `inspect`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jose import jwt
from jose.exceptions import JWTError

ALGORITHM = "RS256"
ISSUER = "og-license-tool"
USER_MODELS = ("named", "concurrent")

# registry-service's default `licensable_components` (settings.py); an
# installation may configure more, so other names only cause a warning.
KNOWN_COMPONENTS = ("workflow-service", "webdav-connector", "migration-service", "cmis-connector")


@dataclass
class LicenseSpec:
    licensee: str
    expires_at: datetime
    user_model: str
    max_users: int | None
    storage_limit_gb: float | None
    document_limit: int | None
    licensed_components: list[str] | None
    installation_id: str | None = None
    not_before: datetime | None = None
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    license_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def build_claims(spec: LicenseSpec) -> dict:
    if spec.user_model not in USER_MODELS:
        raise ValueError(f"user_model muss {' oder '.join(USER_MODELS)} sein")
    if spec.expires_at <= spec.issued_at:
        raise ValueError("Ablaufdatum liegt nicht nach dem Ausstellungszeitpunkt")
    if spec.not_before and spec.not_before >= spec.expires_at:
        raise ValueError("Gültig-ab liegt nicht vor dem Ablaufdatum")
    for name, value in (
        ("max_users", spec.max_users),
        ("storage_limit_gb", spec.storage_limit_gb),
        ("document_limit", spec.document_limit),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"{name} muss größer als 0 sein")
    claims: dict = {
        "iss": ISSUER,
        "jti": spec.license_id,
        "sub": spec.licensee,
        "iat": int(spec.issued_at.timestamp()),
        "exp": int(spec.expires_at.timestamp()),
        "user_model": spec.user_model,
        "max_users": spec.max_users,
        "storage_limit_gb": spec.storage_limit_gb,
        "document_limit": spec.document_limit,
        "licensed_components": spec.licensed_components,
    }
    if spec.not_before is not None:
        claims["nbf"] = int(spec.not_before.timestamp())
    if spec.installation_id:
        claims["installation_id"] = spec.installation_id
    return claims


def sign(claims: dict, private_key_pem: str) -> str:
    return jwt.encode(claims, private_key_pem, algorithm=ALGORITHM)


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def normalize_token(raw: str) -> str:
    """A licence file holds the bare token; tolerate surrounding whitespace."""
    token = raw.strip()
    if token.count(".") != 2:
        raise ValueError(
            "Keine Lizenzdatei: ein JWT besteht aus drei durch Punkte getrennten Teilen"
        )
    return token


def _b64_json(part: str) -> dict:
    padded = part + "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def decode_unverified(token: str) -> tuple[dict, dict]:
    """Header and claims WITHOUT checking the signature - for `inspect` only."""
    header_part, claims_part, _signature = normalize_token(token).split(".")
    try:
        return _b64_json(header_part), _b64_json(claims_part)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Lizenzdatei nicht lesbar: {exc}") from exc


@dataclass
class VerifyResult:
    signature_ok: bool
    key_used: str | None  # "current" | "previous"
    valid: bool
    problems: list[str]
    claims: dict


def verify(
    token: str,
    *,
    public_key_pem: str,
    previous_public_key_pem: str | None = None,
    installation_id: str | None = None,
    now: datetime | None = None,
) -> VerifyResult:
    """The service's acceptance decision for this token.

    Signature: RS256 against the current key, then the optional previous one
    (rotation window, ADR 0084). Validity: installation binding, `exp`, `nbf`
    - in that order, as `usage.compute_usage` reports the first reason.
    """
    token = normalize_token(token)
    now = now or datetime.now(UTC)
    claims: dict | None = None
    key_used: str | None = None
    for label, candidate in (("current", public_key_pem), ("previous", previous_public_key_pem)):
        if not candidate:
            continue
        try:
            claims = jwt.decode(
                token,
                candidate,
                algorithms=[ALGORITHM],
                options={"verify_aud": False, "verify_exp": False, "verify_nbf": False},
            )
            key_used = label
            break
        except JWTError:
            continue
    if claims is None:
        # Show what the token claims if it is still readable; a token whose
        # payload was damaged is just as invalid.
        try:
            _header, unverified = decode_unverified(token)
        except ValueError:
            unverified = {}
        return VerifyResult(
            signature_ok=False,
            key_used=None,
            valid=False,
            problems=["Signatur ungültig für den angegebenen öffentlichen Schlüssel"],
            claims=unverified,
        )

    problems: list[str] = []
    bound_to = claims.get("installation_id")
    if bound_to is not None and installation_id is not None and bound_to != installation_id:
        problems.append(
            f"für eine andere Installation ausgestellt ({bound_to!r}, nicht {installation_id!r})"
        )
    exp = claims.get("exp")
    if exp is not None and now > datetime.fromtimestamp(exp, tz=UTC):
        problems.append("abgelaufen")
    nbf = claims.get("nbf")
    if nbf is not None and now < datetime.fromtimestamp(nbf, tz=UTC):
        problems.append("noch nicht gültig")
    if claims.get("user_model") not in USER_MODELS:
        # The service doesn't reject it, but counts concurrent sessions then.
        problems.append(f"unbekanntes user_model {claims.get('user_model')!r}")
    return VerifyResult(
        signature_ok=True, key_used=key_used, valid=not problems, problems=problems, claims=claims
    )


def _date(epoch: int | float | None) -> str:
    if epoch is None:
        return "–"
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def describe(claims: dict, *, now: datetime | None = None) -> list[tuple[str, str]]:
    """Human-readable rows for a claim set (German, like the admin UI)."""
    now = now or datetime.now(UTC)
    exp = claims.get("exp")
    remaining = ""
    if exp is not None:
        days = (datetime.fromtimestamp(exp, tz=UTC) - now).days
        remaining = f" (noch {days} Tage)" if days >= 0 else f" (seit {-days} Tagen abgelaufen)"

    def limit(value, unit: str = "") -> str:
        return "unbegrenzt" if value is None else f"{value}{unit}"

    components = claims.get("licensed_components")
    return [
        ("Lizenz-ID", str(claims.get("jti", "–"))),
        ("Lizenznehmer", str(claims.get("sub", "–"))),
        ("Ausgestellt", _date(claims.get("iat"))),
        ("Gültig ab", _date(claims.get("nbf")) if "nbf" in claims else "sofort"),
        ("Gültig bis", _date(exp) + remaining),
        ("Installation", claims.get("installation_id") or "nicht gebunden"),
        ("Nutzermodell", str(claims.get("user_model", "–"))),
        ("Max. Nutzende", limit(claims.get("max_users"))),
        ("Speicher", limit(claims.get("storage_limit_gb"), " GB")),
        ("Dokumente", limit(claims.get("document_limit"))),
        (
            "Komponenten",
            "alle lizenzierbaren" if components is None else (", ".join(components) or "keine"),
        ),
    ]
