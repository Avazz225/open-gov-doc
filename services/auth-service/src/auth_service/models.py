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


class LocalSigningKey(Base):
    """Auth-Entkopplung von Keycloak (Post-Roadmap-Feature, Phase 18, ADR
    0063) - eigenes RSA-Schlüsselpaar für Tokens technischer Konten
    (Superuser/Domain-Admins), unabhängig von Keycloak ausgestellt.
    Singleton-Zeile (`id=1`), gleiches Muster wie `FederationIdentity` oben.
    `kid` ist der `kid`-Claim im Token-Header - stabil über Neustarts hinweg
    (kein neuer Schlüssel bei jedem Start), sonst würden bereits
    ausgestellte, noch gültige Tokens plötzlich keinen passenden Schlüssel
    mehr im JWKS finden."""

    __tablename__ = "local_signing_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kid: Mapped[str] = mapped_column(String(64))
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TechnicalAccount(Base):
    """Auth-Entkopplung von Keycloak (Post-Roadmap-Feature, Phase 18, ADR
    0063) - Superuser- und Domain-Admin-Konten leben ab dieser Phase
    ausschließlich hier, nicht mehr als Keycloak-Nutzerkonten. `password_hash`
    ist ein bcrypt-Hash (erstes Mal, dass dieser Service selbst ein Passwort
    hasht - bislang übernahm Keycloak das vollständig). `role_name` ist die
    an `permission-service` zu übermittelnde Rolle (z. B. `domain-admin-
    users`), identisch zum bisherigen Keycloak-Konten-Muster - `NULL` für den
    Superuser (P18-S2), dessen Sonderrechte nicht über eine permission-
    service-Rolle laufen, sondern über direkten Namensvergleich an mehreren
    Stellen im System (z. B. Not-Shutdown, query-services `_is_active_
    superuser`). `enabled`/`expires_at` tragen weiterhin die Break-Glass-
    Semantik für den Superuser (4.6) - jetzt rein app-seitig, ohne
    Keycloak-Attribut."""

    __tablename__ = "technical_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    account_type: Mapped[str] = mapped_column(String(32))
    role_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
