from datetime import UTC, datetime, timedelta

import pytest
from mail_connector import repository


async def _create_message(session, **overrides):
    kwargs = {
        "mailbox_id": "central",
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

    fetched = await repository.get_by_source_uid(session, "central", "uid-1")

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.status == "unassigned"


async def test_get_by_source_uid_returns_none_when_unknown(session):
    assert await repository.get_by_source_uid(session, "central", "does-not-exist") is None


async def test_get_by_source_uid_is_scoped_per_mailbox(session):
    """Since Post-Roadmap Phase 31 Session 12a: `source_uid` is only unique
    WITHIN a mailbox - two different mailboxes may reuse the same
    backend-native UID without colliding."""
    await _create_message(session, mailbox_id="central", source_uid="uid-shared")
    await _create_message(session, mailbox_id="finanzen", source_uid="uid-shared")

    central = await repository.get_by_source_uid(session, "central", "uid-shared")
    finanzen = await repository.get_by_source_uid(session, "finanzen", "uid-shared")

    assert central is not None
    assert finanzen is not None
    assert central.id != finanzen.id


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


async def test_list_messages_filters_by_mailbox_id(session):
    await _create_message(session, mailbox_id="central", source_uid="uid-central")
    await _create_message(session, mailbox_id="finanzen", source_uid="uid-finanzen")

    central = await repository.list_messages(session, mailbox_id="central")
    finanzen = await repository.list_messages(session, mailbox_id="finanzen")
    all_messages = await repository.list_messages(session)

    assert [m.source_uid for m in central] == ["uid-central"]
    assert [m.source_uid for m in finanzen] == ["uid-finanzen"]
    assert len(all_messages) == 2


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


async def test_route_message_updates_mailbox_and_logs_the_hop(session):
    message = await _create_message(session, mailbox_id="central")

    routed = await repository.route_message(
        session, message.id, target_mailbox_id="finanzen", routed_by="bob", reason="Fachbezug"
    )

    assert routed.mailbox_id == "finanzen"
    [entry] = await repository.list_routing_log(session, message.id)
    assert entry.from_mailbox_id == "central"
    assert entry.to_mailbox_id == "finanzen"
    assert entry.routed_by == "bob"
    assert entry.reason == "Fachbezug"


async def test_route_message_multiple_hops_logs_each_in_order(session):
    """ "Postbuch"-Grundlage (P31-S12c) - jeder Hop bleibt als eigener
    Log-Eintrag erhalten, `mailbox_id` spiegelt nur den aktuellen Stand."""
    message = await _create_message(session, mailbox_id="central")

    await repository.route_message(
        session, message.id, target_mailbox_id="finanzen", routed_by="bob", reason=None
    )
    routed_again = await repository.route_message(
        session, message.id, target_mailbox_id="personal", routed_by="carol", reason=None
    )

    assert routed_again.mailbox_id == "personal"
    entries = await repository.list_routing_log(session, message.id)
    assert [(e.from_mailbox_id, e.to_mailbox_id) for e in entries] == [
        ("central", "finanzen"),
        ("finanzen", "personal"),
    ]


async def test_list_routing_log_empty_for_never_routed_message(session):
    message = await _create_message(session)
    assert await repository.list_routing_log(session, message.id) == []


async def test_route_message_raises_on_duplicate_source_uid_at_target(session):
    """Found live during P31-S12b verification: two mailboxes independently
    polling the same physical mail account can each ingest their own copy of
    a message with the same backend-native `source_uid` - routing one into
    the other's mailbox must not surface the composite unique constraint as
    a raw `IntegrityError`."""
    message = await _create_message(session, mailbox_id="central", source_uid="shared-uid")
    await _create_message(session, mailbox_id="finanzen", source_uid="shared-uid")

    with pytest.raises(repository.DuplicateInTargetMailboxError):
        await repository.route_message(
            session, message.id, target_mailbox_id="finanzen", routed_by="bob", reason=None
        )

    unchanged = await repository.get_message(session, message.id)
    assert unchanged.mailbox_id == "central"
    assert await repository.list_routing_log(session, message.id) == []


async def test_search_routing_log_returns_hops_across_messages_newest_first(session):
    """ "Postbuch"-Register (P31-S12c) - im Unterschied zu `list_routing_log`
    (ein Ergebnis pro Nachricht) hier ueber ALLE Nachrichten hinweg."""
    first = await _create_message(session, mailbox_id="central", source_uid="uid-a")
    second = await _create_message(session, mailbox_id="central", source_uid="uid-b")

    await repository.route_message(
        session, first.id, target_mailbox_id="finanzen", routed_by="bob", reason=None
    )
    await repository.route_message(
        session, second.id, target_mailbox_id="personal", routed_by="carol", reason=None
    )

    rows = await repository.search_routing_log(session)

    assert [entry.message_id for entry, _message in rows] == [second.id, first.id]
    assert [message.subject for _entry, message in rows] == [second.subject, first.subject]


async def test_search_routing_log_filters_by_mailbox_id_either_side(session):
    message = await _create_message(session, mailbox_id="central", source_uid="uid-c")
    await repository.route_message(
        session, message.id, target_mailbox_id="finanzen", routed_by="bob", reason=None
    )

    from_side = await repository.search_routing_log(session, mailbox_id="central")
    to_side = await repository.search_routing_log(session, mailbox_id="finanzen")
    unrelated = await repository.search_routing_log(session, mailbox_id="personal")

    assert [e.message_id for e, _m in from_side] == [message.id]
    assert [e.message_id for e, _m in to_side] == [message.id]
    assert unrelated == []


async def test_search_routing_log_filters_by_routed_by(session):
    message = await _create_message(session, mailbox_id="central", source_uid="uid-d")
    await repository.route_message(
        session, message.id, target_mailbox_id="finanzen", routed_by="bob", reason=None
    )

    matching = await repository.search_routing_log(session, routed_by="bob")
    other = await repository.search_routing_log(session, routed_by="carol")

    assert [e.message_id for e, _m in matching] == [message.id]
    assert other == []


async def test_search_routing_log_filters_by_subject_substring(session):
    message = await _create_message(
        session, mailbox_id="central", source_uid="uid-e", subject="Antrag auf Baugenehmigung"
    )
    await repository.route_message(
        session, message.id, target_mailbox_id="finanzen", routed_by="bob", reason=None
    )

    matching = await repository.search_routing_log(session, q="baugenehmigung")
    other = await repository.search_routing_log(session, q="does-not-occur")

    assert [e.message_id for e, _m in matching] == [message.id]
    assert other == []


async def test_search_routing_log_filters_by_date_range(session):
    message = await _create_message(session, mailbox_id="central", source_uid="uid-f")
    await repository.route_message(
        session, message.id, target_mailbox_id="finanzen", routed_by="bob", reason=None
    )
    [entry] = await repository.list_routing_log(session, message.id)

    before = entry.routed_at - timedelta(minutes=1)
    after = entry.routed_at + timedelta(minutes=1)

    since_before = await repository.search_routing_log(session, since=before)
    since_after = await repository.search_routing_log(session, since=after)
    until_after = await repository.search_routing_log(session, until=after)
    until_before = await repository.search_routing_log(session, until=before)

    assert [e.message_id for e, _m in since_before] == [message.id]
    assert since_after == []
    assert [e.message_id for e, _m in until_after] == [message.id]
    assert until_before == []


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
