from datetime import datetime

from pydantic import BaseModel


class InstallationRegister(BaseModel):
    id: str
    display_name: str
    callback_base_url: str
    public_key_pem: str
    version: str
    min_compatible_peer_version: str
    supported_process_types: list[str] = []
    supported_document_types: list[str] = []


class InstallationRegisterOut(BaseModel):
    id: str
    # Nur bei der allerersten Registrierung gesetzt - der Hub speichert den
    # Klartext-Key nie, ein erneuter Aufruf (Update) kann ihn folglich nicht
    # zurückgeben, siehe repository.register_or_update_installation.
    api_key: str | None


class InstallationOut(BaseModel):
    id: str
    display_name: str
    callback_base_url: str
    public_key_pem: str
    version: str
    min_compatible_peer_version: str
    supported_process_types: list[str]
    supported_document_types: list[str]
    registered_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class HandoverCreate(BaseModel):
    # Von der Absenderinstallation selbst erzeugt (nicht vom Hub) - siehe
    # ADR 0028 "Selbst-Loopback": die Installation muss ihre eigene, lokale
    # `FederationTask`-Zeile bereits committet haben, bevor sie den Hub
    # aufruft (der synchron bis zurück in die eigene Installation zustellen
    # kann), sonst wäre die Zeile bei einem verschachtelten Rückruf
    # (z. B. Übergabe an die eigene Installation) noch nicht sichtbar.
    handover_id: str
    to_installation_id: str
    process_type: str
    # Base64-kodiertes, Ende-zu-Ende verschlüsseltes Envelope-JSON (siehe
    # `workflow_service.federation_crypto`) - für den Hub vollständig opak.
    encrypted_payload: str


class HandoverOut(BaseModel):
    id: str
    from_installation_id: str
    to_installation_id: str
    process_type: str
    status: str
    created_at: datetime
    delivered_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class HandoverResultSubmit(BaseModel):
    # "completed" (fachlich erfolgreich abgeschlossen) | "failed" (Zielinstallation
    # konnte den Schritt nicht verarbeiten) - rein informativ für die Absenderseite,
    # der Hub selbst wertet das nicht aus.
    outcome: str
    encrypted_result: str


class PublicKeyOut(BaseModel):
    public_key_pem: str
