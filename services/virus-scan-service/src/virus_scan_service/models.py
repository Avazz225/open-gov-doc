from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("virus_scan")


class ScanResult(Base):
    """Ein durchgeführter Scan (10.3). `document_id` ist beim initialen Upload
    noch unbekannt (der Scan läuft *vor* der Dokumenterstellung, ADR 0010) und
    daher nullable; beim Check-in einer neuen Version ist er bereits bekannt.
    Seit P15-S2 (2.5) wird `document_id` bei einer Freigabe (status="released")
    nachträglich auf das neu angelegte Dokument gesetzt.
    """

    __tablename__ = "scan_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    # "clean" | "infected" | "released" (Fehlalarm geklärt, 2.5, P15-S2) |
    # "purged" (endgültig gelöscht, 2.5, P15-S2)
    status: Mapped[str] = mapped_column(String(16))
    threat_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    engine: Mapped[str] = mapped_column(String(32))
    # Nur bei status="infected" gesetzt gewesen: Objekt-Key im Storage
    # Service, unter dem die infizierte Datei zu Beweiszwecken abgelegt wurde
    # (Quarantäne statt automatischem Löschen, 10.3). Bleibt nach Freigabe/
    # endgültiger Löschung als historische Referenz stehen, auch wenn die
    # Bytes selbst dann nicht mehr existieren - kein Nullen des Felds, damit
    # `GET /scans/{id}` weiterhin nachvollziehbar bleibt (5.3).
    quarantine_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Wer/wann den Quarantäne-Fall aufgelöst hat (freigegeben oder endgültig
    # gelöscht, 2.5, P15-S2) - deckt beide Aktionen mit denselben zwei Spalten
    # ab, analog zum knappen `deleted_by`-Muster der Papierkorb-Familie
    # (P15-S1) statt vier separater release_*/purge_*-Spalten.
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
