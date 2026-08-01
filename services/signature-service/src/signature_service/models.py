from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("signature")


class InternalCa(Base):
    """Selbstsignierte interne Root-CA (3.10, "systeminterne/selbstsignierte
    Schlüssel") - bewusst eine einzelne Zeile mit fester `id=1`, gleiches
    Singleton-Muster wie `OcrConfig`/`SystemMaintenanceMode` (eine Installation,
    keine Mandantentrennung, 3a). Wird beim ersten Start generiert (RSA 2048 +
    selbstsigniertes Root-Zertifikat, siehe `connectors/internal.py`) und danach
    idempotent wiederverwendet - jede spätere Signatur wird mit einem von dieser
    Root ausgestellten, kurzlebigen Leaf-Zertifikat erzeugt (siehe
    `Signature.certificate_serial`)."""

    __tablename__ = "internal_ca"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    certificate_pem: Mapped[bytes] = mapped_column(LargeBinary)
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Signature(Base):
    """Eine einzelne elektronische Signatur (3.10) - gebunden an eine konkrete,
    neu erzeugte Dokumentversion bei document-service (2.1a): das Signieren
    verändert zwangsläufig die PDF-Bytes (PAdES bettet die Signatur in die
    Datei selbst ein), die signierten Bytes werden deshalb als eigenständige,
    für immer erhaltene Version eingecheckt statt die Ursprungsversion zu
    überschreiben - `source_version_number` verweist auf die signierte
    Ausgangsversion, `version_number` auf die neu entstandene, signierte
    Version."""

    __tablename__ = "signature"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(128))
    source_version_number: Mapped[int] = mapped_column(Integer)
    version_number: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(8))
    connector_id: Mapped[str] = mapped_column(String(64))
    signer_principal_id: Mapped[str] = mapped_column(String(128))
    signer_display_name: Mapped[str] = mapped_column(String(256))
    certificate_subject: Mapped[str] = mapped_column(String(512))
    certificate_serial: Mapped[str] = mapped_column(String(64))
    certificate_not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    certificate_not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
