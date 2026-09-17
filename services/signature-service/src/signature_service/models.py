from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("signature")


class InternalCa(Base):
    """Self-signed internal root CA (3.10, "system-internal/self-signed
    keys") - deliberately a single row with a fixed `id=1`, same singleton
    pattern as `OcrConfig`/`SystemMaintenanceMode` (one installation, no
    tenant separation, 3a). Generated on first startup (RSA 2048 +
    self-signed root certificate, see `connectors/internal.py`) and then
    reused idempotently - every later signature is produced with a
    short-lived leaf certificate issued by this root (see
    `Signature.certificate_serial`)."""

    __tablename__ = "internal_ca"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    certificate_pem: Mapped[bytes] = mapped_column(LargeBinary)
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InternalTsa(Base):
    """Internal RFC 3161 timestamp authority certificate (3.10, PAdES-B-LTA,
    Post-Roadmap Phase 41 Session 1) - same singleton pattern as
    `InternalCa`, and issued FROM `InternalCa` (see
    `connectors.internal.issue_tsa_certificate`), so both chain up to the
    same trust root. Generated once on first startup and then reused
    idempotently, same rationale as `InternalCa`: a restart must not
    reissue it, or previously embedded timestamp tokens would no longer
    resolve to a trusted signer."""

    __tablename__ = "internal_tsa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    certificate_pem: Mapped[bytes] = mapped_column(LargeBinary)
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Signature(Base):
    """A single electronic signature (3.10) - bound to a specific, newly
    created document version at document-service (2.1a): signing
    necessarily changes the PDF bytes (PAdES embeds the signature into the
    file itself), so the signed bytes are checked in as a standalone,
    permanently retained version instead of overwriting the source
    version - `source_version_number` refers to the signed source version,
    `version_number` to the newly created, signed version.

    `last_timestamped_at` (Phase 41 Session 1, PAdES-B-LTA/3.10): `NULL`
    until the first periodic archive-timestamp-chain extension
    (`connectors.internal.InternalSelfSignedConnector.extend_timestamp_
    chain`) - the INITIAL `sign()` call already embeds the first archive
    timestamp (`use_pades_lta=True`), so a signature is B-LTA-conformant
    from the moment it's created; this field only tracks LATER,
    periodic re-timestamps (`_run_retimestamp_tick`), which is what
    actually makes it "archival" (a continuously extended timestamp
    chain, not a one-off). `version_number` is updated in place on every
    successful extension, since each one checks in a new document
    version (same "signed bytes get their own version" principle as the
    original signature)."""

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
    last_timestamped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SignatureConfig(Base):
    """Admin-UI-editable connector levels (3.10, post-roadmap phase 22
    session 6, ADR 0091) - single row with a fixed `id=1`, same singleton
    pattern as `InternalCa`/`OcrConfig`. `provider_levels` maps ONLY
    `SignatureProviderConfig.levels` per already env-var-configured
    connector (`{connector_id: [level, ...]}`) - `id`/`type` remain
    structurally fixed (env var, no secrets involved, but new connector
    types need code anyway, see `connectors/__init__.py`). Read fresh from
    the DB on every signing operation (no `app.state` cache), hence
    effective without a restart."""

    __tablename__ = "signature_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_levels: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
