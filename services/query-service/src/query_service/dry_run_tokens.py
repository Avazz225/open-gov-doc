import base64
import hashlib
import hmac
import json
import time


class InvalidDryRunTokenError(Exception):
    """Wird geworfen, wenn ein Dry-Run-Token fehlt, abgelaufen ist oder die
    Signatur nicht passt."""


def _sign(secret: str, message: bytes) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    ).decode("ascii")


def issue_token(
    *, action_type: str, params: dict, principal_id: str, secret: str, ttl_seconds: int
) -> str:
    """Beweist bei `/manipulate/execute`, dass unmittelbar zuvor ein Dry-Run
    mit exakt diesen Parametern lief (Konzept 6.1 Punkt 2, "Dry-Run als
    Standardverhalten"). Bewusst zustandslos (kein DB-Eintrag) - die
    Signatur macht eine serverseitige Ablage ueberfluessig, siehe
    PROGRESS.md fuer die Begruendung gegen eine eigene Tabelle. `action_type`/
    `params` werden im Token selbst transportiert (nicht separat vom Client
    erneut mitgesendet) - der Token IST die Quelle der Wahrheit fuer das,
    was tatsaechlich per Dry-Run geprueft wurde."""
    body = {
        "action_type": action_type,
        "params": params,
        "principal_id": principal_id,
        "expires_at": time.time() + ttl_seconds,
    }
    payload = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True).encode("utf-8")).decode(
        "ascii"
    )
    signature = _sign(secret, payload.encode("ascii"))
    return f"{payload}.{signature}"


def decode(token: str, *, secret: str) -> dict:
    """Prueft Signatur + Ablauf und liefert die im Token eingebetteten
    Angaben (`action_type`, `params`, `principal_id`) zurueck - wirft
    `InvalidDryRunTokenError` bei fehlerhafter/abgelaufener/manipulierter
    Eingabe."""
    try:
        payload, signature = token.split(".", 1)
    except ValueError as exc:
        raise InvalidDryRunTokenError("Malformter Dry-Run-Token") from exc

    expected_signature = _sign(secret, payload.encode("ascii"))
    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidDryRunTokenError("Dry-Run-Token-Signatur ungueltig")

    try:
        body = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidDryRunTokenError("Dry-Run-Token-Inhalt nicht lesbar") from exc

    if body["expires_at"] < time.time():
        raise InvalidDryRunTokenError("Dry-Run-Token abgelaufen - erneuten Dry-Run ausfuehren")
    return body
