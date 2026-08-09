from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("fleet")


class ManagedInstallation(Base):
    """Eine vom Betreiber dieses Fleet-Management-Service überblickte,
    vollständig unabhängige Installation (3a). Anders als
    `federation-hub-service`s ``Installation`` (die nur einen Hash speichert,
    da der Hub den Klartext-Key nie wieder braucht) speichert diese Tabelle
    ``fleet_agent_api_key`` im **Klartext** - dieser Service muss den Schlüssel
    bei jedem ausgehenden Aufruf an die Installation PRÄSENTIEREN, nie selbst
    verifizieren (identische Begründung wie `migration-service`s
    ``PairedInstallation``, siehe dort)."""

    __tablename__ = "managed_installation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    gateway_base_url: Mapped[str] = mapped_column(String(512))
    fleet_agent_api_key: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
