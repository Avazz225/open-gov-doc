from datetime import UTC, datetime

import pytest
from mail_connector import repository


async def _create_message(session, **overrides):
    kwargs = {
        "source_uid": "uid-1",
        "from_address": "buerger@example.com",
        "subject": "Rueckmeldung",
        "body_text": "Hallo",
        "received_at": datetime.now(UTC),
        "match_type": None,
        "match_value": None,
        "proposed_target_type": None,
        "proposed_target_id": None,
        "match_candidates": [],
    }
    kwargs.update(overrides)
    return await repository.create_inbound_message(session, **kwargs)


async def test_create_and_get_by_source_uid(session):
    created = await _create_message(session)

    fetched = await repository.get_by_source_uid(session, "uid-1")

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.status == "unassigned"


async def test_get_by_source_uid_returns_none_when_unknown(session):
    assert await repository.get_by_source_uid(session, "does-not-exist") is None


async def test_message_with_proposed_target_starts_as_proposed_match(session):
    created = await _create_message(
        session,
        source_uid="uid-2",
        match_type="kennzeichen",
        match_value="2026-001",
        proposed_target_type="document",
        proposed_target_id="doc-1",
    )

    assert created.status == "proposed_match"


async def test_get_message_unknown_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_message(session, "does-not-exist")


async def test_add_and_list_attachments(session):
    message = await _create_message(session)
    await repository.add_attachment(
        session,
        message_id=message.id,
        filename="anhang.pdf",
        content_type="application/pdf",
        size_bytes=42,
        scan_id="scan-1",
        scan_status="clean",
        storage_object_key="posteingang/x/anhang.pdf",
    )

    attachments = await repository.list_attachments(session, message.id)

    assert len(attachments) == 1
    assert attachments[0].filename == "anhang.pdf"


async def test_list_messages_filters_by_status(session):
    await _create_message(session, source_uid="uid-a")
    await _create_message(
        session,
        source_uid="uid-b",
        proposed_target_type="document",
        proposed_target_id="doc-1",
        match_type="kennzeichen",
        match_value="2026-001",
    )

    unassigned = await repository.list_messages(session, status="unassigned")
    proposed = await repository.list_messages(session, status="proposed_match")

    assert len(unassigned) == 1
    assert len(proposed) == 1


async def test_mark_confirmed_sets_status_and_confirmer(session):
    message = await _create_message(session)

    confirmed = await repository.mark_confirmed(session, message.id, confirmed_by="bob")

    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_by == "bob"
    assert confirmed.confirmed_at is not None


async def test_mark_rejected_sets_status_and_reason(session):
    message = await _create_message(session)

    rejected = await repository.mark_rejected(session, message.id, rejected_by="bob", reason="Spam")

    assert rejected.status == "rejected"
    assert rejected.rejected_reason == "Spam"


async def test_set_attachment_document_clears_storage_key(session):
    message = await _create_message(session)
    attachment = await repository.add_attachment(
        session,
        message_id=message.id,
        filename="a.pdf",
        content_type="application/pdf",
        size_bytes=1,
        scan_id="scan-1",
        scan_status="clean",
        storage_object_key="posteingang/x/a.pdf",
    )

    await repository.set_attachment_document(session, attachment.id, document_id="doc-99")

    [refreshed] = await repository.list_attachments(session, message.id)
    assert refreshed.resulting_document_id == "doc-99"
    assert refreshed.storage_object_key is None


async def test_create_and_list_outbound_messages(session):
    await repository.create_outbound_message(
        session,
        to_address="extern@example.com",
        subject="Antwort",
        body="Hallo",
        related_document_id=None,
        related_case_id=None,
        sent_by="alice",
        status="sent",
        error_message=None,
    )

    messages = await repository.list_outbound_messages(session)

    assert len(messages) == 1
    assert messages[0].to_address == "extern@example.com"
