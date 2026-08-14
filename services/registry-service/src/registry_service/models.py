from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("registry")


class ServiceInstance(Base):
    """A registered service instance (3.2a).

    ``instance_id`` is assigned by the registering service itself (e.g. a
    UUID per process lifetime) - a restart registers as a new instance, the
    old one becomes stale via ``last_heartbeat_at`` and disappears from the
    active routing table without requiring a separate cleanup process.
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
    # Drain mechanism (10.5/3.8, P10-S2): "active" accepts new requests,
    # "draining" no longer does (see gateway_service.upstream.InstanceResolver),
    # but keeps running untouched until the instance deregisters normally.
    # Set exclusively via POST /instances/{id}/drain - neither registration
    # (except for an actual new row) nor heartbeat change it.
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Sensor catalog (10.1, P11-S1): purely a passed-through self-declaration
    # ("which sensors do I offer") - registry-service itself does not run
    # any sensor configuration/aggregation, that's done by `monitoring-service`
    # via GET /instances (which simply reads this field along with the rest).
    sensors: Mapped[list[dict]] = mapped_column(JSON, default=list)
