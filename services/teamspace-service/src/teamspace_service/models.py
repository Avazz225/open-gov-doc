import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("teamspace")


class Teamspace(Base):
    """Selbstverwalteter, dauerhafter Team-Arbeitsbereich (2.5, P14-S6) - bewusst
    von der Umlaufmappe (2.3, `case-service`) zu unterscheiden: eine Umlaufmappe
    ist eine sequenzielle Weiterleitung mit Abschluss, ein Teamspace ein
    dauerhaftes Gruppenarbeitsgebiet ohne definiertes Ende. Jeder authentifizierte
    Principal kann einen neuen Teamspace anlegen (keine administrative
    Vorabeinrichtung nötig, Konzept 2.5 wörtlich) - `repository.create_teamspace`
    legt dabei automatisch einen dedizierten Wurzelordner bei `folder-service`
    an (`root_folder_id`, opake Referenz, kein FK über Service-Grenzen hinweg)
    und macht die anlegende Person zum ersten Mitglied mit
    `can_manage_members=True`."""

    __tablename__ = "teamspace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    root_folder_id: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamspaceMember(Base):
    """Mitgliedschaft (2.5, P14-S6) - das eigentliche, von der übrigen RBAC (4.1)
    unabhängige Zugriffsregime dieses Service: jeder Endpunkt außer der
    Neuanlage eines Teamspace verlangt eine bestehende Mitgliedschaftszeile für
    den aufrufenden `X-DMS-Principal` (siehe `main.py._require_member`).
    `can_manage_members` deckt bewusst sowohl Mitgliederverwaltung (Einladen/
    Entfernen) als auch das Löschen des gesamten Teamspace ab - EIN Flag statt
    zweier getrennter Berechtigungsstufen, da Konzept 2.5 selbst keine
    Unterscheidung zwischen beiden trifft ("kann diese Verwaltung aber an
    weitere Mitglieder delegieren"). Bewusste Grenze: `principal_id` ist
    ausschließlich ein einzelner Nutzer, KEIN Gruppen-Prinzipal - "group" ist im
    gesamten Projekt kein real durchgesetztes Konzept (weder in Keycloak noch in
    `permission-service`s `RoleAssignment.principal_type`, dort nur ein
    unbenutzter Kommentarwert), siehe `docs/services/teamspace-service.md`
    "Bewusste Grenzen"."""

    __tablename__ = "teamspace_member"
    __table_args__ = (UniqueConstraint("teamspace_id", "principal_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teamspace_id: Mapped[str] = mapped_column(ForeignKey("teamspace.teamspace.id"), index=True)
    principal_id: Mapped[str] = mapped_column(String(255), index=True)
    can_manage_members: Mapped[bool] = mapped_column(Boolean, default=False)
    invited_by: Mapped[str] = mapped_column(String(255))
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamspaceAppointment(Base):
    """Gemeinsamer Termin (2.5, P14-S6) - vollständig neues Konzept, keine
    Wiederverwendung von `workflow-service`s SLA-Geschäftskalendern (P14-S5,
    dort reine Werktage-Arithmetik für Fristberechnung, keine Nutzer sichtbaren
    Termine). Bewusst vollständig geteilt (kein Ersteller-exklusives Bearbeiten/
    Löschen) - jedes Mitglied darf jeden Termin anlegen/löschen, passend zum
    Konzept-Wortlaut "gemeinsame Termine" ohne Erwähnung individueller
    Eigentümerschaft."""

    __tablename__ = "teamspace_appointment"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teamspace_id: Mapped[str] = mapped_column(ForeignKey("teamspace.teamspace.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamspaceContact(Base):
    """Gemeinsamer Kontakt (2.5, P14-S6) - bewusst ein einfacher, freier
    Adressbucheintrag je Teamspace, NICHT der künftige, installationsweite
    Kontakte-Sonderbereich aus Konzept 2.5 (dort: Verzeichnis der eigenen
    Belegschaft auf Basis von `auth-service`, Phase 15, noch nicht gebaut) -
    beide sind laut Konzept-Tabelle klar getrennte Sonderbereiche mit
    unterschiedlichem Zweck (installationsweites Personenverzeichnis vs.
    teamspace-lokale freie Notizen, z. B. auch für teamfremde externe
    Ansprechpartner)."""

    __tablename__ = "teamspace_contact"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teamspace_id: Mapped[str] = mapped_column(ForeignKey("teamspace.teamspace.id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
