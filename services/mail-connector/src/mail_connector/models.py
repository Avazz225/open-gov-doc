from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("mail_connector")


class InboundMessage(Base):
    """Eine über den Posteingang abgeholte, noch nicht (oder bereits)
    zugeordnete Nachricht (2.5/10.3). `source_uid` ist der backend-eigene,
    stabile Bezeichner (POP3-UIDL) - Grundlage der Idempotenz-Prüfung, damit
    derselbe Poll-Tick eine bereits verarbeitete Nachricht nicht doppelt
    anlegt."""

    __tablename__ = "inbound_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_uid: Mapped[str] = mapped_column(String(255), unique=True)
    from_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(998))
    body_text: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # "unassigned" (kein/mehrdeutiger Treffer) | "proposed_match" (genau ein
    # Kennzeichen-/Vorgangsnummer-Treffer, wartet auf Bestätigung) |
    # "confirmed" (zugeordnet, Dokument(e) angelegt) | "rejected" (von der
    # Poststelle verworfen, z. B. Spam).
    status: Mapped[str] = mapped_column(String(16), default="unassigned")
    match_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # "kennzeichen"|"vorgangsnummer"
    match_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposed_target_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # "document"|"case"
    proposed_target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Alle im Betreff/Text gefundenen Kandidaten-Token (auch bei 0/N
    # Treffern) - Transparenz für die manuelle Zuordnung durch die
    # Poststelle, siehe matching.py.
    match_candidates: Mapped[list] = mapped_column(JSON, default=list)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class InboundAttachment(Base):
    """Ein Anhang einer `InboundMessage` (2.5/10.3) - durchläuft bei der
    Ankunft bereits den verpflichtenden Virenscan (siehe
    `virus_scan_client.py`); ein sauberer Anhang wird bis zur Zuordnung unter
    `storage_object_key` zwischengelagert, ein infizierter landet
    ausschließlich in der bereits bestehenden Quarantäne (P15-S2) - keine
    doppelte Ablage."""

    __tablename__ = "inbound_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mail_connector.inbound_message.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    scan_id: Mapped[str] = mapped_column(String(36))
    scan_status: Mapped[str] = mapped_column(String(16))  # "clean" | "infected"
    # Nur bei scan_status="clean" gesetzt - Zwischenlagerung bis zur
    # Zuordnung, danach gelöscht (siehe repository/main.py confirm/assign).
    storage_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resulting_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class OutboundMessage(Base):
    """Postausgang (2.5) - jede über `POST /outbound` versandte externe
    Korrespondenz, auditierbar mit optionalem Bezug auf das auslösende
    Dokument/die auslösende Umlaufmappe."""

    __tablename__ = "outbound_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    to_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(998))
    body: Mapped[str] = mapped_column(String)
    related_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    related_case_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_by: Mapped[str] = mapped_column(String(128))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # "sent" | "failed"
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
