"""Verifikation signierter Lizenzdateien (9.2) - ADR 0032: JWT/RS256 ueber
`python-jose[cryptography]`, statisch eingebetteter oeffentlicher Schluessel
statt eines JWKS-Abrufs (kein Online-Dienst fuer Lizenzverifikation
vorgesehen, Installationen koennen luftgetrennt betrieben werden)."""

from jose import jwt
from jose.exceptions import JWTError


class InvalidLicenseError(Exception):
    pass


def decode(token: str, *, public_key_pem: str, previous_public_key_pem: str | None = None) -> dict:
    """Prueft Signatur sowie `exp`/`nbf` (jose-Standardverhalten) und gibt die
    Claims zurueck. Ein abgelaufenes, aber signaturgueltiges Token wird
    bewusst NICHT hier abgelehnt - `jose` wirft bei abgelaufenem `exp`
    ebenfalls einen `JWTError`, daher wird `exp` separat mit
    `options={"verify_exp": False}` toleriert und die eigentliche
    Gueltigkeitspruefung dem Aufrufer ueberlassen (siehe usage.py) - eine
    installierte, aber abgelaufene Lizenz ist ein Statuszustand, kein
    Upload-Fehler (siehe PROGRESS.md Architekturentscheidung).

    ``previous_public_key_pem`` (seit Post-Roadmap Phase 21 Session 1,
    ADR 0084) - Uebergangsfenster fuer eine Lizenzgeber-Schluesselrotation:
    ``public_key_pem`` wird zuerst versucht, erst bei dessen Fehlschlag der
    optionale Vorgaenger-Schluessel. Damit bleiben bereits unter dem alten
    Schluessel signierte, installierte Lizenzen waehrend der Uebergangsfrist
    weiterhin gueltig, waehrend neu ausgestellte Lizenzen bereits den neuen
    Schluessel verwenden koennen. Der Betreiber entfernt
    ``previous_public_key_pem`` wieder, sobald alle betroffenen
    Installationen eine unter dem neuen Schluessel signierte Lizenz
    hochgeladen haben - das beendet die Uebergangsfrist ("dann invalidiert")."""
    candidates = [public_key_pem]
    if previous_public_key_pem:
        candidates.append(previous_public_key_pem)

    last_error: JWTError | None = None
    for candidate in candidates:
        try:
            return jwt.decode(
                token,
                candidate,
                algorithms=["RS256"],
                options={"verify_aud": False, "verify_exp": False, "verify_nbf": False},
            )
        except JWTError as exc:
            last_error = exc
    raise InvalidLicenseError(f"Lizenzsignatur ungueltig: {last_error}")
