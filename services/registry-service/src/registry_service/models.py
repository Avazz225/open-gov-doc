from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("registry")


class ServiceInstance(Base):
    """Eine registrierte Service-Instanz (3.2a).

    ``instance_id`` wird vom registrierenden Service selbst vergeben (z. B.
    UUID pro Prozess-Lebenszeit) - ein Neustart registriert sich als neue
    Instanz, die alte veraltet über ``last_heartbeat_at`` und verschwindet
    aus der aktiven Routingtabelle, ohne dass ein separater Aufräumprozess
    nötig ist.
    """

    __tablename__ = "service_instance"

    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    service_type: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    health_endpoint: Mapped[str] = mapped_column(String(512))
    address: Mapped[str] = mapped_column(String(512))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Drain-Mechanismus (10.5/3.8, P10-S2): "active" nimmt neue Anfragen an,
    # "draining" nicht mehr (siehe gateway_service.upstream.InstanceResolver),
    # laeuft aber unangetastet weiter, bis die Instanz sich regulaer
    # deregistriert. Wird ausschliesslich ueber POST /instances/{id}/drain
    # gesetzt - weder Registrierung (ausser bei einer echten neuen Zeile)
    # noch Heartbeat aendern ihn.
    status: Mapped[str] = mapped_column(String(16), default="active")
