from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("federation")


class HubIdentity(Base):
    """Eigenes Signaturschlüsselpaar des Hub (RSA-2048, `cryptography`, gleiche
    Konvention wie `signature-service`s interne CA, ADR 0025) - bewusst eine
    einzelne Zeile mit fester ``id=1``, gleiches Singleton-Muster wie
    `InternalCa`. Der Hub signiert damit jede an eine Installation zugestellte
    Nachricht (``X-Federation-Hub-Signature``), sodass die empfangende
    Installation echt verifizieren kann, dass die Zustellung tatsächlich vom
    Hub stammt - ohne dass irgendwo ein geteiltes Geheimnis im Klartext
    gespeichert werden müsste (siehe ADR 0028)."""

    __tablename__ = "hub_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Installation(Base):
    """Ein Eintrag im Adressbuch (7.4) - eine bei diesem Hub angemeldete,
    vollständig unabhängige Installation. ``id`` ist die von der Installation
    selbst gewählte öffentliche Kennung (nicht vom Hub vergeben).
    ``public_key_pem`` ist der öffentliche Schlüssel, mit dem **andere**
    Installationen für diese hier bestimmte Payloads verschlüsseln (Ende-zu-
    Ende, der Hub selbst besitzt nie den privaten Schlüssel dazu) - seit
    P13-S4 (ADR 0039) dient derselbe Schlüssel zusätzlich als kryptografische
    Identität dieser Installation: jede schreibende Anfrage an den Hub muss
    mit dem passenden privaten Schlüssel signiert sein (ersetzt das zuvor
    verwendete ``api_key_hash``-Feld, ein reines geteiltes Geheimnis). Ein
    Schlüsselwechsel läuft ausschließlich über ``POST
    /installations/{id}/rotate-key`` (signiert mit dem noch aktuellen
    Schlüssel) - eine reguläre Re-Registrierung überschreibt ``public_key_pem``
    nicht mehr stillschweigend. ``revoked_at``/``revoked_reason`` erlauben
    einem Hub-Betreiber, eine kompromittierte Installation sofort zu sperren
    (``POST /installations/{id}/revoke``), unabhängig davon, ob die
    Installation selbst noch signieren kann."""

    __tablename__ = "installation"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    callback_base_url: Mapped[str] = mapped_column(String(512))
    public_key_pem: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32))
    min_compatible_peer_version: Mapped[str] = mapped_column(String(32))
    supported_process_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    supported_document_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Handover(Base):
    """Metadaten einer einzelnen Übergabe-Vermittlung (7.4: "protokolliert nur
    Metadaten des Vermittlungsvorgangs ... nicht die Dokumentinhalte selbst") -
    bewusst **kein** Feld für den (Ende-zu-Ende verschlüsselten) Payload selbst,
    der wird synchron weitergeleitet, nie hier persistiert."""

    __tablename__ = "handover"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    to_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    process_type: Mapped[str] = mapped_column(String(256))
    # "pending" -> "delivered"|"delivery_failed" -> "completed"|"result_delivery_failed"
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
