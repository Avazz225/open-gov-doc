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


class Delegation(Base):
    """Stellvertretung bei Abwesenheit (4.4a, P14-S11) - zeitlich befristete,
    umfangsbegrenzte Übertragung der Aufgabenwahrnehmung von einer
    abwesenden Person (``delegator_principal_id``) an eine Stellvertretung
    (``deputy_principal_id``). Bewusst KEIN Identitätswechsel: die
    Stellvertretung handelt weiterhin unter dem eigenen Konto - dieser
    Datensatz ist nur die Grundlage für die Berechtigungsprüfung
    (``GET /delegations/check``, von workflow-service beim Aufgabenabschluss
    aufgerufen) und den Audit-Vermerk "im Auftrag von" (5.3), kein Login-
    Mechanismus. Wird nie hart gelöscht (``revoked_at``/``revoked_by``
    dokumentieren die vorzeitige Beendigung), gleiches Muster wie
    ``ScopeLock`` oben bzw. document-services ``ShareLink`` (P14-S10)."""

    __tablename__ = "delegation"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    delegator_principal_id: Mapped[str] = mapped_column(String(128), index=True)
    deputy_principal_id: Mapped[str] = mapped_column(String(128), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # None = keine Einschränkung auf dieser Dimension - sind alle drei None,
    # gilt die "vollständige Übernahme aller offenen Aufgaben" (4.4a).
    # `scope_process_definition_ids` ist der einzige Wortlaut-Dimension, die
    # sich beim heutigen Konsumenten (workflow-service) tatsächlich ohne
    # zusätzlichen Cross-Service-Umweg prüfen lässt (Prozessinstanzen
    # tragen `process_definition_id` bereits direkt) - die beiden anderen
    # Felder werden dennoch mitgespeichert (Konzept-Wortlaut "Objekttypen ...
    # Ordnerbereiche"), auch wenn sie aktuell von keinem Endpunkt ausgewertet
    # werden (siehe ADR 0048, "Offene Punkte").
    scope_object_type_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    scope_process_definition_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    scope_folder_resource_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Group(Base):
    """Admin-anlegbare Gruppen (Post-Roadmap Phase 22 Session 2) - ergänzen
    die seit Phase 19 Session 2 bestehende, hartkodierte "everyone"-Gruppe
    (siehe ``repository.EVERYONE_*``) um echte, admin-verwaltete Gruppen mit
    expliziter Mitgliedschaft (``GroupMembership``). Eine Rollenzuweisung an
    eine dieser Gruppen (``RoleAssignment.principal_type="group"``,
    ``principal_id=<group.id>``) gilt für jedes eingetragene Mitglied, ohne
    die Rolle jedem einzeln zuzuweisen - anders als "everyone" (implizite
    Mitgliedschaft, keine eigene Zeile) braucht jede echte Gruppe explizite
    ``GroupMembership``-Zeilen, siehe ``repository._collect_effective_roles``.
    """

    __tablename__ = "group"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupMembership(Base):
    __tablename__ = "group_membership"
    __table_args__ = (UniqueConstraint("group_id", "principal_id", name="uq_group_membership"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("permission.group.id"), index=True
    )
    principal_id: Mapped[str] = mapped_column(String(128), index=True)


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
