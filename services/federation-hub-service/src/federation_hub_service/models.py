from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("federation")


class HubIdentity(Base):
    """The hub's own signing key pair (RSA-2048, `cryptography`, same
    convention as `signature-service`'s internal CA, ADR 0025) - deliberately
    a single row with a fixed ``id=1``, same singleton pattern as
    `InternalCa`. The hub uses it to sign every message delivered to an
    installation (``X-Federation-Hub-Signature``), so the receiving
    installation can genuinely verify that the delivery actually came from
    the hub - without needing to store a shared secret in plaintext anywhere
    (see ADR 0028).

    ``ca_certificate_pem`` (since Post-Roadmap Phase 21 Session 2, ADR 0085)
    - the same key pair additionally wrapped as a self-signed X.509 root CA
    certificate (analogous to `signature-service`'s `InternalCa`, ADR 0025) -
    NOT a separate key pair, just an additional certificate wrapper around
    the same private key. This lets the hub issue each installation a
    certificate it signs, with a limited validity period
    (`Installation.certificate_pem`), instead of only storing a raw,
    indefinitely valid public key. Deliberately NOT real transport mTLS (see
    ADR 0039 "No room for a real PKI in this project" - whose reasoning still
    applies unchanged, no service in this repo terminates TLS itself) - the
    certificate check still happens at the application level, in addition to
    the existing signature check."""

    __tablename__ = "hub_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    ca_certificate_pem: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Installation(Base):
    """An entry in the address book (7.4) - a fully independent installation
    registered with this hub. ``id`` is the public identifier chosen by the
    installation itself (not assigned by the hub). ``public_key_pem`` is the
    public key that **other** installations use to encrypt payloads destined
    for this one (end-to-end, the hub itself never has the corresponding
    private key) - since P13-S4 (ADR 0039) the same key additionally serves
    as this installation's cryptographic identity: every write request to
    the hub must be signed with the matching private key (replaces the
    previously used ``api_key_hash`` field, a plain shared secret). A key
    change goes exclusively through ``POST /installations/{id}/rotate-key``
    (signed with the still-current key) - a regular re-registration no
    longer silently overwrites ``public_key_pem``. ``revoked_at``/
    ``revoked_reason`` allow a hub operator to immediately lock a compromised
    installation (``POST /installations/{id}/revoke``), regardless of
    whether the installation itself can still sign.

    ``certificate_pem`` (since Post-Roadmap Phase 21 Session 2, ADR 0085) - a
    time-limited X.509 certificate issued by the hub that binds
    ``public_key_pem`` (certificate-pinning equivalent: the installation AND
    any third party can use the hub CA to verify that exactly this key
    belongs to exactly this ``id``, with a clear validity boundary instead of
    indefinite validity). `authenticate_signed_request` re-verifies, on
    every request, the full chain up to the hub CA AND the validity window
    from the certificate bytes themselves
    (`crypto_utils.verify_installation_certificate`) -
    ``certificate_not_after`` is only a derived, denormalized display value
    (admin UI/migration detection), not an independent security check. Both
    fields are reset on registration and on every key rotation (otherwise the
    old key would keep a certificate for a no-longer-current key). ``NULL``
    for installations that were registered before this session AND have not
    yet been rotated/reissued - `authenticate_signed_request` skips the
    certificate check in this case (grandfathering), but still requires the
    already-existing signature check."""

    __tablename__ = "installation"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    callback_base_url: Mapped[str] = mapped_column(String(512))
    public_key_pem: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32))
    min_compatible_peer_version: Mapped[str] = mapped_column(String(32))
    supported_process_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    supported_document_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_not_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Handover(Base):
    """Metadata of a single handover mediation (7.4: "logs only the metadata
    of the mediation process ... not the document contents themselves") -
    deliberately **no** field for the (end-to-end encrypted) payload itself,
    which is forwarded synchronously and never persisted here. This still
    holds since Post-Roadmap Phase 20 Session 5 (ADR 0081) - a payload that
    still needs to be redelivered via retry is kept only EPHEMERALLY in
    process memory (`app.state.pending_handover_payloads`), never in this
    table - a restart of the hub during an open retry window therefore loses
    the ability to automatically redeliver (see
    docs/services/federation-hub-service.md "Open Points")."""

    __tablename__ = "handover"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    to_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    process_type: Mapped[str] = mapped_column(String(256))
    # "pending" -> "delivered"|"pending_retry"->...->"delivery_failed" ->
    # "completed"|"result_delivery_failed" (Post-Roadmap Phase 20 Session 5,
    # ADR 0081: "pending_retry" is new, "delivery_failed" is now only reached
    # after max_handover_delivery_attempts is exhausted instead of, as
    # before, immediately on every single failure).
    status: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
