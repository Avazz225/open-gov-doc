"""Gemeinsame (major, minor)-Versionsparsung (7.4) - von `schemas.py`
(Validierung bei der Registrierung) UND `repository.py`
(Kompatibilitätsvergleich) genutzt, um eine doppelte Implementierung zu
vermeiden. Bewusst ein einfaches Zahlenschema statt einer SemVer-Bibliothek
(siehe ADR 0028).

P13-S3-Fund: vor dieser Validierung akzeptierte `POST /installations` jeden
beliebigen `version`-String anstandslos - ein nicht-numerischer Wert (z. B.
durch einen Tippfehler oder ein missverstandenes Versionsschema) wurde
klaglos gespeichert und ließ erst später, bei einer völlig anderen
Installations-Paarung, `POST /handovers` mit einem unbehandelten
`ValueError` (HTTP 500) abstürzen - ein Fehler an der Stelle, an der er
tatsächlich entsteht (Registrierung), statt an einer beliebigen späteren
Vermittlung."""


class InvalidVersionFormatError(ValueError):
    pass


def parse_version(value: str) -> tuple[int, int]:
    parts = value.split(".")
    try:
        major = int(parts[0]) if parts and parts[0] else 0
        minor = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    except ValueError as exc:
        raise InvalidVersionFormatError(
            f"Ungültiges Versionsformat {value!r} - erwartet z. B. '9.3'"
        ) from exc
    return major, minor
