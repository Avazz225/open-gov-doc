"""Schema-Versionierung des Konfigurationsdokuments (7.3: "Versionierung des
Konfigurationsschemas selbst, damit Export aus einer älteren Version in eine
neuere importiert werden kann - mit Migrationslogik bei Schemaänderungen").

Es gibt bislang nur `SCHEMA_VERSION = "1.0"` (siehe `schemas.py`), also noch
keine reale Migration zu schreiben - `MIGRATIONS` ist der vorgesehene
Erweiterungspunkt für den Tag, an dem sich das Dokumentformat ändert: ein
Eintrag `"1.0"` würde eine Funktion `dict -> dict` registrieren, die ein
Dokument dieser Version auf die NÄCHSTHÖHERE Version anhebt (`upgrade_to_current`
wendet alle nötigen Schritte nacheinander an). Ein unbekannter, nicht in
`MIGRATIONS` erreichbarer `schema_version`-Wert wird abgelehnt (`422`), statt
stillschweigend falsch interpretiert zu werden."""

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
