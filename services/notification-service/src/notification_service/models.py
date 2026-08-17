from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("notification")


class Notification(Base):
    """A single delivery attempt (Concept 7.1, P6-S2). `recipient` is heterogeneous
    depending on `channel` (email address / username-or-lane-name / webhook URL) -
    deliberately a single generic field instead of three channel-specific columns, to
    keep the model simple for this baseline. The first attempt happens synchronously
    on creation (`repository.create_and_send`) - since Post-Roadmap Phase 20 Session
    3 (ADR 0079), failed `email`/`webhook` deliveries are no longer immediately
    terminal: `status` stays `"failed"` (retry-capable) with an increasing `attempts`
    and a `next_retry_at` set via full-jitter backoff, which a new, independent poll
    loop works through (delivery itself remains synchronous/inline in the NATS
    handler or the `POST /notifications` endpoint - only the RETRY runs
    asynchronously, so as not to block the handler). Only after
    `max_notification_attempts` unsuccessful attempts does `status` switch to the
    real terminal status `failed_permanent`. `in_app` has no real delivery step and
    is therefore never retry-capable - always immediately `"sent"`."""

    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailTemplate(Base):
    """Configurable email content per use case (post-roadmap phase 30,
    ADR 0111) - mirrors permission-service's `ApprovalActionConfig` keyed-
    discriminator + "no row = fallback" shape (`models.py:78-94` there): no
    matching row for `(use_case, recipient_domain_pattern)` means the
    existing hardcoded f-string in `consumer.py` applies implicitly (see
    `resolve_template` in `templates.py`). `use_case` is the event's
    `event_type` (already the dispatch key in `consumer.py`), a fixed,
    closed catalog - unlike `ApprovalActionConfig`'s open free-text
    `action_type`, since `consumer.py`'s handlers are a fixed set of
    branches, not an open-ended list of callers. `recipient_domain_pattern
    IS NULL` is the catch-all row for a use case (any domain); a non-NULL
    value narrows to exactly that domain. `subject_template`/`body_template`
    use the same `{placeholder}` syntax as `object-type-service`'s
    `kennzeichen_format` (`str.format()`-rendered, see `templates.py`)."""

    __tablename__ = "email_template"
    __table_args__ = (UniqueConstraint("use_case", "recipient_domain_pattern"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    use_case: Mapped[str] = mapped_column(String(128))
    recipient_domain_pattern: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_template: Mapped[str] = mapped_column(String(512))
    body_template: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
