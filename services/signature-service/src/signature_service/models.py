from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, Integer, LargeBinary, String, Text
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


INCOMING_VALIDATION_CONFIG_ID = 1


class IncomingValidationConfig(Base):
    """Admin settings for validating incoming signatures (P92-S1, ADR 0248) -
    singleton like `SignatureConfig`. `revocation_mode`: `soft-fail` (an
    unreachable revocation source doesn't stop a pass) or `hard-fail` (it
    makes the result INDETERMINATE). `outbound_fetching`: whether OCSP/CRL/
    AIA may be fetched at all (through the fenced client)."""

    __tablename__ = "incoming_validation_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revocation_mode: Mapped[str] = mapped_column(String(16), default="soft-fail")
    outbound_fetching: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class IncomingTrustAnchor(Base):
    """A CA certificate an admin uploaded as a trust anchor (ADR 0248
    Decision 3, "installation anchors") - for example a partner authority's
    PKI. Results based on it say "vertrauenswürdig (Installation)", never
    "qualifiziert"."""

    __tablename__ = "incoming_trust_anchor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(256))
    certificate_der: Mapped[bytes] = mapped_column(LargeBinary)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    subject: Mapped[str] = mapped_column(String(1024))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(128))


class IncomingSignatureValidation(Base):
    """One signature in one document version, as seen by one validation run
    (ADR 0248 Decision 5). Rows are never updated: a re-validation adds a run
    with a new `run_id`, so the history of statements stays visible."""

    __tablename__ = "incoming_signature_validation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(16))
    format: Mapped[str] = mapped_column(String(16))
    position: Mapped[str | None] = mapped_column(String(256), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    indication: Mapped[str] = mapped_column(String(16))
    sub_indication: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qualification: Mapped[str] = mapped_column(String(8))
    trust_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signer_subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    signer_issuer: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    signer_serial: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_signing_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    best_signature_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    coverage: Mapped[str] = mapped_column(String(32))
    modification_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    technical_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    revocation_mode: Mapped[str] = mapped_column(String(16))
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IncomingSignatureSummary(Base):
    """The latest run's verdict per document version, for lists and badges:
    the worst indication, the best proven qualification, and whether any
    signature doesn't cover what is shown. Replaced by each new run (the
    per-signature rows above keep the history)."""

    __tablename__ = "incoming_signature_summary"

    document_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36))
    signature_count: Mapped[int] = mapped_column(Integer)
    worst_indication: Mapped[str] = mapped_column(String(16))
    best_qualification: Mapped[str] = mapped_column(String(8))
    changed_after_signing: Mapped[bool] = mapped_column(Boolean)
    truncated: Mapped[bool] = mapped_column(Boolean)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
