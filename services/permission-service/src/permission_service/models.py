from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("permission")


class ResourceNode(Base):
    """Generischer Knoten einer Ressourcen-Hierarchie (aktuell: Ordner).

    Wird nicht von diesem Service selbst erzeugt (mit Ausnahme des
    Wurzelknotens), sondern über Struktur-Events des jeweiligen
    Owner-Service (Folder Service, P3-S3) synchron gehalten - siehe
    ``structure_consumer.py``.
    """

    __tablename__ = "resource_node"

    resource_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("permission.resource_node.resource_id"), nullable=True
    )
    resource_type: Mapped[str] = mapped_column(String(64), default="folder")
    # Vererbung ein/aus (4.1) - False bricht die Vererbungskette an diesem Knoten ab.
    inherit: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    __table_args__ = (
        UniqueConstraint(
            "principal_type", "principal_id", "role_id", "resource_id", name="uq_assignment"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    principal_type: Mapped[str] = mapped_column(String(16))  # "user" | "group"
    principal_id: Mapped[str] = mapped_column(String(128), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("permission.role.id"))
    resource_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("permission.resource_node.resource_id"), index=True
    )


class EffectivePermissionCache(Base):
    """Materialisierter Cache (4.1) - wird bei jeder Rechte-/Strukturänderung
    vollständig geleert (siehe repository.invalidate_cache) statt granular je
    betroffenem Teilbaum invalidiert. Bewusste Vereinfachung für den Start;
    Ergebnis bleibt korrekt, nur der Neuaufbau nach einer Änderung ist etwas
    breiter als unbedingt nötig.
    """

    __tablename__ = "effective_permission_cache"

    principal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    roles: Mapped[list[str]] = mapped_column(JSON)
    permissions: Mapped[list[str]] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
