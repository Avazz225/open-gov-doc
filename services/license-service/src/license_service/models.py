from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("license")

INSTALLED_LICENSE_ID = 1


class InstalledLicense(Base):
    """Aktuell installierte Lizenzdatei (9.2) - Singleton-Zeile, gleiches
    Muster wie query-service's `ManipulationModeStatus` (P8-S2). `raw_token`
    ist das vollstaendige, signierte JWT wie hochgeladen (Wiederverifikation
    jederzeit moeglich, kein separates Vertrauen in gecachte Claims noetig).
    `last_status_snapshot` haelt fest, welche Grenzwerte/Ablauf-Schwellen der
    Poll-Loop beim letzten Tick bereits gemeldet hat - verhindert Event-Spam
    bei jedem Tick (siehe poll_loop.py)."""

    __tablename__ = "installed_license"

    id: Mapped[int] = mapped_column(primary_key=True, default=INSTALLED_LICENSE_ID)
    raw_token: Mapped[str] = mapped_column(Text)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    installed_by: Mapped[str] = mapped_column()
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_status_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
