import pytest
from notification_service import repository
from notification_service.templates import (
    UnknownPlaceholderError,
    render_template,
    resolve_template,
    template_placeholders,
)


def test_render_template_substitutes_placeholders():
    assert render_template("Hallo {name}!", name="Alice") == "Hallo Alice!"


def test_render_template_raises_for_missing_value():
    with pytest.raises(UnknownPlaceholderError):
        render_template("Hallo {name}!")


def test_template_placeholders_extracts_unique_names():
    assert template_placeholders("{a} und {b} und wieder {a}") == {"a", "b"}


async def test_resolve_template_prefers_exact_domain_over_catchall(session):
    await repository.upsert_email_template(
        session,
        use_case="document.deletion.reminder",
        recipient_domain_pattern=None,
        subject_template="Catchall: {title}",
        body_template="B",
    )
    await repository.upsert_email_template(
        session,
        use_case="document.deletion.reminder",
        recipient_domain_pattern="example.com",
        subject_template="Domain-specific: {title}",
        body_template="B",
    )

    resolved = await resolve_template(
        session, use_case="document.deletion.reminder", recipient="alice@example.com"
    )

    assert resolved is not None
    assert resolved.subject_template == "Domain-specific: {title}"


async def test_resolve_template_falls_back_to_catchall_for_a_different_domain(session):
    await repository.upsert_email_template(
        session,
        use_case="document.deletion.reminder",
        recipient_domain_pattern=None,
        subject_template="Catchall: {title}",
        body_template="B",
    )
    await repository.upsert_email_template(
        session,
        use_case="document.deletion.reminder",
        recipient_domain_pattern="example.com",
        subject_template="Domain-specific: {title}",
        body_template="B",
    )

    resolved = await resolve_template(
        session, use_case="document.deletion.reminder", recipient="alice@other.org"
    )

    assert resolved is not None
    assert resolved.subject_template == "Catchall: {title}"


async def test_resolve_template_returns_none_without_any_configured_row(session):
    resolved = await resolve_template(
        session, use_case="document.deletion.reminder", recipient="alice@example.com"
    )
    assert resolved is None


async def test_resolve_template_returns_none_for_a_non_email_recipient_without_catchall(session):
    resolved = await resolve_template(
        session, use_case="workflow.task.escalated", recipient="unassigned"
    )
    assert resolved is None


async def test_resolve_template_matches_catchall_even_for_a_non_email_recipient(session):
    await repository.upsert_email_template(
        session,
        use_case="workflow.task.escalated",
        recipient_domain_pattern=None,
        subject_template="Catchall",
        body_template="B",
    )

    resolved = await resolve_template(
        session, use_case="workflow.task.escalated", recipient="unassigned"
    )

    assert resolved is not None
