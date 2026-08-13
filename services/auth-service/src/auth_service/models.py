from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("auth")


class FederationIdentity(Base):
    """Eigene Federation-Identität für die optionale föderierte Kontaktsuche
    (2.5/7.4, P15-S4) - erstes eigenes Postgres-Schema dieses Service
    überhaupt (bislang zustandslos, siehe README/docs/services/
    auth-service.md), bewusst gerechtfertigt durch genau eine Singleton-Zeile
    (``id=1``, gleiches Muster wie `workflow_service.FederationIdentity`).

    Registriert sich als EIGENER, von `workflow-service`s Federation-Hub-
    Teilnahme unabhängiger Eintrag im selben Adressbuch (frischer `uuid4()`
    statt eines geteilten `installation_id`, gleiches Erzeugungsmuster wie
    dort) - der Hub kennt "Installation" nur als generischen, von jedem
    Service unabhängig registrierbaren Adressbuch-Eintrag, keine
    fest-verdrahtete 1:1-Zuordnung zu einer bestimmten Installation. Zwei
    Einträge (einer für Workflow-Föderation, einer für Kontaktsuche) je
    physischer Installation ist eine bewusste, dokumentierte Vereinfachung
    gegenüber einer einzigen, service-übergreifend geteilten Identität -
    siehe ADR 0054 "Konsequenzen"."""

    __tablename__ = "federation_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(128))
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SsoConfig(Base):
    """SSO/automatischer Login (Post-Roadmap-Feature) - installationsweiter
    Schalter, gleiches Einzelzeilen-Muster wie document-services
    `ShareLinkConfig`. `enabled=False` (Default) bedeutet: `login/page.tsx`
    zeigt weiterhin unverändert das Passwort-Formular, keine automatische
    Weiterleitung zu Keycloak."""

    __tablename__ = "sso_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
