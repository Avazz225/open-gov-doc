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


class ScopeLock(Base):
    """Bereichssperre (4.7): sperrt einen Ressourcen-Teilbaum vorübergehend für
    reguläre Nutzer, unabhängig von den sonst geltenden RBAC-Rechten - ein
    eigenständiges, RBAC überlagerndes statt veränderndes Konstrukt. Wird nie
    hart gelöscht (``released_at``/``released_by`` dokumentieren die
    Aufhebung), damit der Verlauf auditierbar bleibt (5.3)."""

    __tablename__ = "scope_lock"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("permission.resource_node.resource_id"), index=True
    )
    locked_by: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # False (Default) blockiert nur Schreibzugriffe, True zusätzlich Lesezugriffe.
    blocks_read: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ApprovalActionConfig(Base):
    """Vier-Augen-Konfiguration je Aktionstyp (4.3): "konfigurierbar pro
    Aktionstyp, nicht global erzwungen" - fehlt eine Zeile für einen
    Aktionstyp, gilt implizit ``requires_approval=False`` (siehe
    ``repository.get_approval_config``)."""

    __tablename__ = "approval_action_config"

    action_type: Mapped[str] = mapped_column(String(128), primary_key=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optionale Verschärfung von 4.3 auf 4.6 (Break-Glass): ist gesetzt, müssen
    # sowohl Initiator als auch Genehmiger diese Capability an der Wurzel-
    # ressource halten (zusätzlich zur Initiator≠Genehmiger-Regel) - ohne das
    # wäre "irgendeine zweite Person" (4.3) zu schwach für "zwei verschiedene
    # Mitglieder einer Berechtigungsgruppe" (4.6).
    required_permission: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRequest(Base):
    """Generischer Freigabe-Request (4.3): eine Aktion mit aktivem Vier-Augen-
    Flag legt hier eine Zeile an, statt sofort ausgeführt zu werden. Nach
    Genehmigung führt NICHT dieser Service die eigentliche Aktion aus,
    sondern publiziert ``permission.approval.approved`` - der initiierende
    Service (oder dieser Service selbst als Konsument seines eigenen
    Events, siehe ``approval_consumer.py``) führt die Aktion anhand von
    ``payload`` aus."""

    __tablename__ = "approval_request"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_type: Mapped[str] = mapped_column(String(128), index=True)
    initiated_by: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class SystemMaintenanceMode(Base):
    """Systemweite Notfallsperre & Wartungsmodus (4.8, P6-S6) - Singleton
    (feste ``id=1``, gleiches Muster wie ``OcrConfig``/``GuardConfig`` in
    anderen Services). Wird nie gelöscht, nur umgeschaltet - ``triggered_by``/
    ``lifted_by`` bleiben als Audit-Spur auch nach Aufhebung stehen, bis zur
    nächsten Aktivierung."""

    __tablename__ = "system_maintenance_mode"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
