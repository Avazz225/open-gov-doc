from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
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


class InstallationGroup(Base):
    """Eine benannte Gruppe/"Welle" (3a-Erweiterung, P13-S2b: "Installationen
    werden zu benannten Gruppen zusammengefasst") - reine Etikettierung,
    keine eigene Ausführungslogik. Eine Installation kann in mehreren Gruppen
    Mitglied sein (z. B. "Testinstallationen" UND "Kunde-Nord-Cluster")."""

    __tablename__ = "installation_group"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstallationGroupMember(Base):
    __tablename__ = "installation_group_member"

    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fleet.installation_group.id"), primary_key=True
    )
    installation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fleet.managed_installation.id"), primary_key=True
    )


class UpdatePlan(Base):
    """Deklarativer, versionierter Update-Plan (3a: "eine strukturierte
    Abfolge benannter Schritte ... der Plan ist versioniert ... nicht Code").
    ``steps`` ist eine geordnete JSON-Liste von
    ``{name, step_type, requires_approval}`` - ``step_type`` ist entweder
    ``"verify"`` (automatisch, siehe `main._run_verify_step`) oder ``"gate"``
    (wird durch einen expliziten `POST .../mark-done` bestätigt - steht für
    jeden Schritt, dessen tatsächliche Ausführung außerhalb dieses Service
    liegt: Bereichssperre/Wartungsmodus setzen (4.7/4.8), den eigentlichen
    Rolling Update ausführen (10.5), ein Backup ziehen (10.4), final
    freigeben). Siehe ADR 0038 für die Begründung dieser bewussten Grenze -
    dieser Service löst diese Aktionen nicht selbst auf einer fremden
    Installation aus, er koordiniert nur, WANN/IN WELCHER REIHENFOLGE sie
    passieren sollen und verfolgt das Ergebnis."""

    __tablename__ = "update_plan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[str] = mapped_column(String(32))
    steps: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Rollout(Base):
    """Eine Welle: ein `UpdatePlan` angewendet auf eine Menge von
    Installationen (Gruppen-Mitglieder ∪ ``include`` − ``exclude``, 3a:
    "Installationen lassen sich gezielt ein-/ausschließen"). Muss explizit
    gestartet werden (``POST /rollouts/{id}/start``) - keine automatische
    Kettenreaktion zur nächsten Welle, die ist ein eigener, separat
    angelegter/gestarteter `Rollout` (3a: "die nächste Welle startet erst
    nach Bestätigung der vorherigen")."""

    __tablename__ = "rollout"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("fleet.update_plan.id"))
    name: Mapped[str] = mapped_column(String(256))
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("fleet.installation_group.id"), nullable=True
    )
    include: Mapped[list] = mapped_column(JSON, default=list)
    exclude: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


class InstallationRun(Base):
    """Fortschritt einer einzelnen Installation innerhalb eines `Rollout`
    (3a: "Fehlerklasse, aktueller Schritt und Fortschritt je Installation").
    ``status`` trägt entweder ``"pending"``/``"completed"`` oder eine der
    fünf Konzept-Fehlerentscheidungen wörtlich (``retry_later``/
    ``wait_external``/``manual_required``/``recoverable_failed``/
    ``fatal_contract``) - siehe `orchestration.py`."""

    __tablename__ = "installation_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rollout_id: Mapped[str] = mapped_column(String(36), ForeignKey("fleet.rollout.id"))
    installation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fleet.managed_installation.id")
    )
    current_step_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32))
    last_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nur bei ``requires_approval``-Schritten belegt - wer den Schritt als
    # erledigt vorgeschlagen hat, damit `approve()` erzwingen kann, dass eine
    # ANDERE Person freigibt (strukturelle statt kryptografische Trennung
    # zwischen Vorschlag/Freigabe, siehe ADR 0038 - fleet-management-service
    # führt keine eigene Nutzerverwaltung).
    proposed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
