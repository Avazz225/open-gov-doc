"""Verifikation signierter Lizenzdateien (9.2) - ADR 0032: JWT/RS256 ueber
`python-jose[cryptography]`, statisch eingebetteter oeffentlicher Schluessel
statt eines JWKS-Abrufs (kein Online-Dienst fuer Lizenzverifikation
vorgesehen, Installationen koennen luftgetrennt betrieben werden)."""

from jose import jwt
from jose.exceptions import JWTError


class InvalidLicenseError(Exception):
    pass


def decode(token: str, *, public_key_pem: str) -> dict:
    """Prueft Signatur sowie `exp`/`nbf` (jose-Standardverhalten) und gibt die
    Claims zurueck. Ein abgelaufenes, aber signaturgueltiges Token wird
    bewusst NICHT hier abgelehnt - `jose` wirft bei abgelaufenem `exp`
    ebenfalls einen `JWTError`, daher wird `exp` separat mit
    `options={"verify_exp": False}` toleriert und die eigentliche
    Gueltigkeitspruefung dem Aufrufer ueberlassen (siehe usage.py) - eine
    installierte, aber abgelaufene Lizenz ist ein Statuszustand, kein
    Upload-Fehler (siehe PROGRESS.md Architekturentscheidung)."""
    try:
        return jwt.decode(
            token,
            public_key_pem,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_exp": False, "verify_nbf": False},
        )
    except JWTError as exc:
        raise InvalidLicenseError(f"Lizenzsignatur ungueltig: {exc}") from exc
