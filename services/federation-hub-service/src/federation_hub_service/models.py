from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text
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
    holds since Post-Roadmap Phase 20 Session 5 (ADR 0081): a payload that
    still needs to be redelivered via retry is NOT a column on this table -
    that invariant is unchanged. What DID change is where it lives instead:
    since Post-Roadmap Phase 73 Session 3 (ADR 0213), it is persisted in the
    separate `HandoverRetryPayload` table below (previously it was kept only
    EPHEMERALLY in process memory, `app.state.pending_handover_payloads`,
    which a hub restart during an open retry window silently lost). A
    restart now no longer loses the ability to automatically redeliver -
    see `HandoverRetryPayload`'s own docstring and
    docs/services/federation-hub-service.md "Retry & Backoff"."""

    __tablename__ = "handover"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    to_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    process_type: Mapped[str] = mapped_column(String(256))
    # "pending" -> "delivered"|"pending_retry"->...->"delivery_failed" ->
    # "result_pending_retry"->...->"completed"|"result_delivery_failed"
    # (Post-Roadmap Phase 20 Session 5, ADR 0081: "pending_retry" is new,
    # "delivery_failed" is now only reached after
    # max_handover_delivery_attempts is exhausted instead of, as before,
    # immediately on every single failure. Phase 40 Session 3: the same
    # retry-then-give-up shape is now ALSO applied to the separate return
    # path, `POST /handovers/{id}/result`'s hub->origin-installation
    # delivery - "result_pending_retry" is new, `attempts`/`next_retry_at`
    # above remain exclusively for the forward-delivery leg, see
    # `result_attempts`/`result_next_retry_at` below for the mirrored
    # counters on the result leg).
    status: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Return-path retry (Phase 40 Session 3) - mirrors `attempts`/
    # `next_retry_at` above exactly, but for the hub's outbound delivery of
    # the RESULT to `from_installation_id` (inside `submit_handover_result`),
    # independent of the forward-delivery leg's own counters. The
    # `encrypted_result` payload that still needs retrying, like the forward
    # payload, is persisted in `HandoverRetryPayload` (`leg="result"`) since
    # ADR 0213 - same table, same restart-survival guarantee, doubled for
    # this second leg.
    result_attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HandoverRetryPayload(Base):
    """Persisted retry cache for the end-to-end encrypted handover payload
    (Post-Roadmap Phase 73 Session 3, ADR 0213) - replaces the two
    in-process dicts (`app.state.pending_handover_payloads`/
    `..._result_payloads`) that held this same data only EPHEMERALLY since
    ADR 0081/Phase 40 Session 3. Those dicts meant a hub restart during an
    open retry window silently and permanently lost the ability to
    automatically redeliver - `_run_retry_tick`/`_run_result_retry_tick`
    would find the `Handover` row due for retry but no cached payload for
    it, log `federation_handover_retry_payload_lost`, and give up. Moving
    the cache here (keyed by `(handover_id, leg)`, `leg` one of "forward"/
    "result" for the two independent delivery directions on the same
    handover) closes that gap: the payload now survives a restart exactly
    like every other piece of retry state already does.

    Deliberately a SEPARATE table from `Handover`, not a nullable payload
    column on it (see ADR 0213 "Rationale"): `Handover` is a permanent audit
    record with a "metadata only" design intent (7.4) that this change does
    not reverse - the retry payload's own lifecycle is transient by
    definition (it exists only between a failed attempt and the next
    successful one, then is deleted), which is a different lifecycle from
    `Handover`'s own. `ON DELETE CASCADE` on `handover_id` means
    `repository.purge_stale_handovers`'s existing plain bulk `DELETE` on
    terminal `Handover` rows needs no Python-side change to also clean up
    any orphaned payload row. This does NOT weaken ADR 0028's end-to-end
    encryption model - the hub still never decrypts `payload`, it stores the
    exact same opaque ciphertext bytes it previously only held in RAM, just
    durably now."""

    __tablename__ = "handover_retry_payload"

    handover_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("federation.handover.id", ondelete="CASCADE"), primary_key=True
    )
    leg: Mapped[str] = mapped_column(String(16), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
