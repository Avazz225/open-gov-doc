from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("audit")


class AuditEvent(Base):
    """An immutable, hash-chained audit entry (concept 5.3).

    ``hash`` = sha256(``prev_hash`` + canonical JSON of the remaining
    fields) - any subsequent modification of an entry breaks the chain
    from that point on, which ``verify_chain`` detects (tamper-resistance
    even under direct DB access, see 5.3).
    """

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(255), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_name: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    # Acting person (first-class instead of an ad-hoc payload convention,
    # since P7-S2) - see AuditMeta.actor_field_cutover_id for the cutover
    # versioning that prevents this new field from breaking the existing
    # hash chain for already-existing rows.
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Delegation during absence (4.4a, since P14-S11) - see
    # AuditMeta.on_behalf_of_field_cutover_id for the same cutover
    # versioning as the actor field above (P7-S2), for the identical
    # reason.
    on_behalf_of: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AuditMeta(Base):
    """Singleton configuration row (``id=1``, the same pattern as
    ``KennzeichenConfig``/``RetentionConfig`` in other services) - holds the
    cutover point for the ``actor`` field introduced in P7-S2: rows with
    ``id <= actor_field_cutover_id`` were hashed BEFORE the field was
    introduced and must continue to be treated without ``actor`` in the
    canonical JSON when recomputing (``verify_chain``), otherwise the hash
    chain breaks retroactively for the entire old history.
    ``on_behalf_of_field_cutover_id`` (since P14-S11) is the same versioning
    for the new ``on_behalf_of`` field (4.4a) - an independent cutover
    value, since both fields were introduced at different points in time."""

    __tablename__ = "audit_meta"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_field_cutover_id: Mapped[int] = mapped_column()
    on_behalf_of_field_cutover_id: Mapped[int] = mapped_column(default=0)
