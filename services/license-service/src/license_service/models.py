from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, Text
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


LICENSE_DISPLAY_CONFIG_ID = 1


class LicenseDisplayConfig(Base):
    """How the admin UI presents the licence (P91-S6) - a singleton.
    `show_upgrade_hints`: show what a licence extension would add (the
    licence page's component list, muted sidebar entries). On by default;
    some operators don't want such hints, so it can be switched off."""

    __tablename__ = "license_display_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=LICENSE_DISPLAY_CONFIG_ID)
    show_upgrade_hints: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
