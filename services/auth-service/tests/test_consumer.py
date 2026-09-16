from auth_service import ad_group_mapping, consumer, superuser
from auth_service.models import AdGroupRoleCompositeRule, AdGroupRoleMapping
from dms_eventbus_client import Event


def _approved_event(action_type: str, payload: dict | None = None) -> bytes:
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-1",
            "action_type": action_type,
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": payload or {},
        },
    )
    return event.to_bytes()


async def test_approved_activation_enables_superuser_and_publishes(session_factory):
    await superuser.ensure_superuser_account(session_factory)
    try:
        published = []

        async def fake_publish(event_type, payload, actor=None):
            published.append((event_type, payload))

        handler = consumer.make_handler(
            session_factory, activation_minutes=30, publish_event=fake_publish
        )

        await handler(_approved_event("auth.superuser.activate"))

        active, _ = await superuser.get_status(session_factory)
        assert active is True
        assert len(published) == 1
        assert published[0][0] == "auth.superuser.activated"
        assert published[0][1]["request_id"] == "req-1"
    finally:
        await superuser.deactivate(session_factory)


async def test_unrelated_action_type_is_ignored(session_factory):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )

    await handler(_approved_event("permission.scope_lock.create"))

    assert published == []


async def test_missing_superuser_account_is_logged_not_raised(session_factory):
    """Regression (gleiches Prinzip wie P6-S4s KeyError-Lehre): ein Konsument
    darf nie an unerwartetem Zustand crashen, sonst bleibt die NATS-Nachricht
    unbestätigt und wird endlos erneut zugestellt. `_clean_tables` (conftest,
    autouse) hat `technical_account` bereits geleert - kein Konto existiert
    zu Beginn dieses Tests."""
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )

    await handler(_approved_event("auth.superuser.activate"))  # darf nicht raisen

    assert published == []


async def test_approved_ad_group_mapping_create_executes_and_publishes(session_factory):
    """Post-Roadmap Phase 39 Session 3 (ADR 0153) - gleiches Muster wie
    `test_approved_activation_enables_superuser_and_publishes`, nur mit
    `auth.ad_group_role_mapping.create`."""
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )

    await handler(
        _approved_event(
            "auth.ad_group_role_mapping.create",
            {"ad_group_name": "sales", "role_name": "dms-sales-role"},
        )
    )

    async with session_factory() as session:
        mappings = await ad_group_mapping.list_mappings(session)
    mapping = next(m for m in mappings if m.ad_group_name == "sales")
    assert mapping.role_name == "dms-sales-role"
    assert mapping.created_by == "alice"  # initiated_by, see _approved_event
    assert published == [
        (
            "auth.ad_group_role_mapping.created",
            {"id": mapping.id, "ad_group_name": "sales", "role_name": "dms-sales-role"},
        )
    ]


async def test_approved_ad_group_mapping_delete_executes_and_publishes(session_factory):
    async with session_factory() as session:
        mapping = await ad_group_mapping.create_mapping(
            session, ad_group_name="finance", role_name="dms-finance-role", created_by="admin"
        )
        await session.commit()
        mapping_id = mapping.id

    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )
    await handler(_approved_event("auth.ad_group_role_mapping.delete", {"mapping_id": mapping_id}))

    async with session_factory() as session:
        remaining = await session.get(AdGroupRoleMapping, mapping_id)
    assert remaining is None
    assert published == [
        (
            "auth.ad_group_role_mapping.deleted",
            {"id": mapping_id, "ad_group_name": "finance", "role_name": "dms-finance-role"},
        )
    ]


async def test_ad_group_mapping_delete_with_unknown_id_is_logged_not_raised(session_factory):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )

    await handler(
        _approved_event("auth.ad_group_role_mapping.delete", {"mapping_id": 999999999})
    )  # darf nicht raisen

    assert published == []


async def test_approved_ad_group_composite_rule_create_executes_and_publishes(session_factory):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )

    await handler(
        _approved_event(
            "auth.ad_group_role_composite_rule.create",
            {"role_name": "dms-dual-role", "ad_group_names": ["sales", "finance"]},
        )
    )

    async with session_factory() as session:
        rules = await ad_group_mapping.list_composite_rules(session)
    rule = next(r for r in rules if r["role_name"] == "dms-dual-role")
    assert sorted(rule["ad_group_names"]) == ["finance", "sales"]
    assert published == [
        (
            "auth.ad_group_role_composite_rule.created",
            {
                "id": rule["id"],
                "role_name": "dms-dual-role",
                "ad_group_names": ["sales", "finance"],
                "created_at": rule["created_at"],
                "created_by": "alice",
            },
        )
    ]


async def test_approved_ad_group_composite_rule_delete_executes_and_publishes(session_factory):
    async with session_factory() as session:
        rule = await ad_group_mapping.create_composite_rule(
            session,
            role_name="dms-dual-role",
            ad_group_names=["sales", "finance"],
            created_by="admin",
        )
        await session.commit()
        rule_id = rule["id"]

    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )
    await handler(_approved_event("auth.ad_group_role_composite_rule.delete", {"rule_id": rule_id}))

    async with session_factory() as session:
        remaining = await session.get(AdGroupRoleCompositeRule, rule_id)
    assert remaining is None
    assert published == [
        (
            "auth.ad_group_role_composite_rule.deleted",
            {
                "id": rule_id,
                "role_name": "dms-dual-role",
                "ad_group_names": ["sales", "finance"],
            },
        )
    ]
