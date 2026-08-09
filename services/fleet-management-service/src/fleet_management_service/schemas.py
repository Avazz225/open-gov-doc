from datetime import datetime

from pydantic import BaseModel


class ManagedInstallationCreate(BaseModel):
    display_name: str
    gateway_base_url: str
    # Optional: der Betreiber kann den auf der Installationsseite bereits per
    # DMS_FLEET_AGENT_API_KEY gesetzten Wert hier eintragen, oder leer lassen
    # und diesen Service einen erzeugen lassen (dann muss der Betreiber den
    # zurückgegebenen Wert umgekehrt auf die Installation übertragen) - exakt
    # dasselbe Muster wie migration-service's `PairedInstallationCreate`.
    fleet_agent_api_key: str | None = None


class ManagedInstallationOut(BaseModel):
    id: str
    display_name: str
    gateway_base_url: str
    created_at: datetime
    updated_at: datetime


class ManagedInstallationCreateOut(ManagedInstallationOut):
    """Nur die Erstantwort enthält den Klartext-Schlüssel - danach nie wieder
    (gleiche Konvention wie `federation-hub-service`s `InstallationRegisterOut`
    bzw. `migration-service`s `PairedInstallationCreateOut`)."""

    fleet_agent_api_key: str


class InstallationStatusOut(BaseModel):
    """Aggregierter Überblick (3a: "grundlegende Health-Übersicht") - live
    abgefragt, nichts davon wird hier persistiert. ``reachable=False`` bei
    jedem Netzwerk-/Protokollfehler statt einer geworfenen Exception, damit
    eine nicht erreichbare Installation nicht die Übersicht der übrigen
    verhindert (gleiches Prinzip wie andere Poll-Schleifen in diesem Projekt,
    z. B. `license-service`s `poll_loop.py`)."""

    id: str
    display_name: str
    reachable: bool
    installation_id: str | None = None
    installation_display_name: str | None = None
    license_status: dict | None = None
    error: str | None = None


class LicenseUploadRequest(BaseModel):
    license_token: str


class ProvisionRequest(BaseModel):
    """`config_document` ist bewusst ein rohes `dict` (identisches Format wie
    `config-service`s eigener Export/Import, 7.3) statt eines eigenen,
    zweiten Konfigurationsschemas - dieser Service kuratiert keine
    Vorlagen-Bibliothek (das ist Phase 17, Konzept §14), er reicht nur
    zentral weiter, was der Betreiber mitgibt."""

    config_document: dict
    categories: list[str] | None = None
