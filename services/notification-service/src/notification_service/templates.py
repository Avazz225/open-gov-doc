import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.models import EmailTemplate

# Same `{placeholder}` convention as object-type-service's
# `kennzeichen_format` (post-roadmap phase 30, ADR 0111) - `str.format()`-
# rendered, so an unknown/missing placeholder fails loudly (`KeyError`)
# rather than silently producing a template with a literal `{gap}` in it.
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class UnknownPlaceholderError(Exception):
    pass


def template_placeholders(template_str: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(template_str))


def render_template(template_str: str, **values: str) -> str:
    try:
        return template_str.format(**values)
    except KeyError as exc:
        raise UnknownPlaceholderError(
            f"Vorlage enthält einen Platzhalter, für den kein Wert übergeben wurde: {exc}"
        ) from exc


def _domain_of(recipient: str) -> str | None:
    if "@" not in recipient:
        return None
    return recipient.rsplit("@", 1)[-1].lower() or None


# Fixed, closed catalog of `use_case`s (post-roadmap phase 30, ADR 0111) -
# deliberate deviation from ApprovalActionConfig's open free-text
# `action_type`: `consumer.py`'s handlers are a fixed set of branches, not
# an open-ended list of callers, so a catalog endpoint listing exactly these
# (rather than "whatever a caller happens to have used before") is more
# useful for an admin UI. `{link}`/`{recipient}` (where present) come from
# `build_resource_link()`/the notification's own recipient, not the raw
# event payload - every other placeholder is a payload field already used
# by the existing hardcoded f-string in `consumer.py`.
EMAIL_TEMPLATE_USE_CASES: list[dict[str, object]] = [
    {
        "use_case": "workflow.task.escalated",
        "description": "SLA-Überschreitung eines Tasks (7.1)",
        "placeholders": ["task_name", "business_key", "instance_id", "link"],
    },
    {
        "use_case": "workflow.federation.inbound_received",
        "description": "Eingehende föderierte Übergabe (7.4)",
        "placeholders": ["from_installation_id", "process_type", "instance_id"],
    },
    {
        "use_case": "document.deletion.reminder",
        "description": "Löschfrist-Erinnerung für ein Dokument (5.2a)",
        "placeholders": ["title", "document_id", "retention_until", "action", "link"],
    },
    {
        "use_case": "folder.deletion.reminder",
        "description": "Löschfrist-Erinnerung für einen Ordner (5.2a)",
        "placeholders": ["name", "folder_id", "retention_until", "action", "link"],
    },
    {
        "use_case": "document.lock.reminder",
        "description": "Erinnerung: Dokument seit längerem gesperrt (4.2, neu seit Phase 30)",
        "placeholders": ["title", "document_id", "locked_by", "link"],
    },
    {
        "use_case": "auth.superuser.activated",
        "description": "Superuser Break-Glass aktiviert (4.6)",
        "placeholders": ["expires_at"],
    },
    {
        "use_case": "permission.maintenance_mode.activated",
        "description": "Systemweite Notfallsperre ausgelöst (4.8)",
        "placeholders": ["triggered_by", "reason"],
    },
    {
        "use_case": "license.limit_exceeded",
        "description": "Lizenz-Nutzungsgrenze überschritten (9.2)",
        "placeholders": ["dimension", "current", "limit"],
    },
    {
        "use_case": "license.expiring_soon",
        "description": "Lizenz läuft bald ab (9.2)",
        "placeholders": ["days_remaining"],
    },
    {
        "use_case": "license.invalid",
        "description": "Lizenz ungültig (9.2)",
        "placeholders": ["reason"],
    },
]


async def resolve_template(
    session: AsyncSession, *, use_case: str, recipient: str
) -> EmailTemplate | None:
    """Three-step resolution (post-roadmap phase 30, ADR 0111): an exact
    `(use_case, domain)` row wins over the `(use_case, NULL)` catch-all row,
    which wins over `None` (the caller then keeps its own hardcoded
    default - see `consumer.py`'s migrated handlers). Returns `None`
    unconditionally if `recipient` carries no `@` (e.g. an in-app
    notification's lane name / `"unassigned"`) - domain-based selection is
    only meaningful for email."""
    domain = _domain_of(recipient)
    if domain:
        result = await session.execute(
            select(EmailTemplate).where(
                EmailTemplate.use_case == use_case,
                EmailTemplate.recipient_domain_pattern == domain,
            )
        )
        exact = result.scalar_one_or_none()
        if exact is not None:
            return exact

    result = await session.execute(
        select(EmailTemplate).where(
            EmailTemplate.use_case == use_case,
            EmailTemplate.recipient_domain_pattern.is_(None),
        )
    )
    return result.scalar_one_or_none()
