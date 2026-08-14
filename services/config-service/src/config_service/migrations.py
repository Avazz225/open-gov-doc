"""Schema versioning of the configuration document (7.3: "Versioning the
configuration schema itself, so an export from an older version can be
imported into a newer one - with migration logic for schema changes").

So far there is only `SCHEMA_VERSION = "1.0"` (see `schemas.py`), so there is
no real migration to write yet - `MIGRATIONS` is the intended
extension point for the day the document format changes: an
entry `"1.0"` would register a `dict -> dict` function that raises a
document of that version to the NEXT-HIGHER version (`upgrade_to_current`
applies all necessary steps in sequence). An unknown `schema_version` value
not reachable in `MIGRATIONS` is rejected (`422`) instead of
being silently misinterpreted."""

from collections.abc import Callable

from config_service.schemas import SCHEMA_VERSION

MIGRATIONS: dict[str, Callable[[dict], dict]] = {}


class UnsupportedSchemaVersionError(Exception):
    pass


def upgrade_to_current(doc: dict) -> dict:
    version = doc.get("schema_version")
    seen = set()
    while version != SCHEMA_VERSION:
        if version in seen:
            raise UnsupportedSchemaVersionError(
                f"Migrationspfad für schema_version {version!r} bildet einen Zyklus"
            )
        seen.add(version)
        migrate = MIGRATIONS.get(version)
        if migrate is None:
            raise UnsupportedSchemaVersionError(
                f"schema_version {version!r} wird nicht unterstützt "
                f"(aktuell: {SCHEMA_VERSION!r}, kein Migrationspfad registriert)"
            )
        doc = migrate(doc)
        version = doc.get("schema_version")
    return doc
