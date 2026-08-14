"""Shared (major, minor) version parsing (7.4) - used by `schemas.py`
(validation at registration) AND `repository.py` (compatibility comparison)
to avoid a duplicate implementation. Deliberately a simple numeric scheme
instead of a SemVer library (see ADR 0028).

P13-S3 finding: before this validation, `POST /installations` accepted any
arbitrary `version` string without complaint - a non-numeric value (e.g. due
to a typo or a misunderstood version scheme) was stored without complaint
and only later crashed `POST /handovers` with an unhandled `ValueError`
(HTTP 500) during a completely unrelated installation pairing - an error at
the point where it actually originates (registration), instead of at some
arbitrary later mediation."""


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
