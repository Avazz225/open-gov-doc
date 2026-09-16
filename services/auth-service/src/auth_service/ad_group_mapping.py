"""AD/Keycloak group -> internal role mapping (4.4, P24-S2). Extended
Post-Roadmap Phase 39 Session 3 (ADR 0153) with composite (AND) rules and a
configurable default role for unmapped groups - see `models.
AdGroupRoleCompositeRule`/`AdGroupRoleCompositeRuleGroup`/
`AdGroupMappingDefaultRole` for the data model rationale.

Pure data-access/resolution functions for `models.AdGroupRoleMapping` and
its Phase 39 Session 3 siblings - `main.py` wires these up into the admin
CRUD endpoints (`/ad-group-mappings`, `/ad-group-composite-rules`,
`/ad-group-mappings/default-role`) and the call in `GET /me`.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service.models import (
    AdGroupMappingDefaultRole,
    AdGroupRoleCompositeRule,
    AdGroupRoleCompositeRuleGroup,
    AdGroupRoleMapping,
)

_DEFAULT_ROLE_CONFIG_ID = 1


class MappingNotFoundError(Exception):
    pass


class CompositeRuleNotFoundError(Exception):
    pass


class TooFewGroupsError(Exception):
    """Raised when a composite rule is created with fewer than 2 groups -
    a single-group rule would be a redundant duplicate of the plain
    `AdGroupRoleMapping` mechanism, so it's rejected rather than silently
    accepted as a no-op composite rule."""


async def list_mappings(session: AsyncSession) -> list[AdGroupRoleMapping]:
    result = await session.execute(
        select(AdGroupRoleMapping).order_by(
            AdGroupRoleMapping.ad_group_name, AdGroupRoleMapping.role_name
        )
    )
    return list(result.scalars().all())


async def create_mapping(
    session: AsyncSession, *, ad_group_name: str, role_name: str, created_by: str | None
) -> AdGroupRoleMapping:
    mapping = AdGroupRoleMapping(
        ad_group_name=ad_group_name,
        role_name=role_name,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    session.add(mapping)
    await session.flush()
    await session.refresh(mapping)
    return mapping


async def delete_mapping(session: AsyncSession, mapping_id: int) -> AdGroupRoleMapping:
    mapping = await session.get(AdGroupRoleMapping, mapping_id)
    if mapping is None:
        raise MappingNotFoundError(f"Mapping {mapping_id} nicht gefunden")
    await session.delete(mapping)
    await session.flush()
    return mapping


async def list_composite_rules(session: AsyncSession) -> list[dict]:
    """Returns each rule together with its linked group names (a plain
    dict instead of the ORM row, since this project's convention - see
    every other `models.py` in this codebase - avoids ORM relationships in
    favor of explicit, separate queries joined in Python)."""
    rules = (
        (
            await session.execute(
                select(AdGroupRoleCompositeRule).order_by(AdGroupRoleCompositeRule.id)
            )
        )
        .scalars()
        .all()
    )
    groups_by_rule = await _groups_by_rule(session, [rule.id for rule in rules])
    return [
        {
            "id": rule.id,
            "role_name": rule.role_name,
            "ad_group_names": groups_by_rule.get(rule.id, []),
            "created_at": rule.created_at,
            "created_by": rule.created_by,
        }
        for rule in rules
    ]


async def _groups_by_rule(session: AsyncSession, rule_ids: list[int]) -> dict[int, list[str]]:
    if not rule_ids:
        return {}
    result = await session.execute(
        select(
            AdGroupRoleCompositeRuleGroup.rule_id, AdGroupRoleCompositeRuleGroup.ad_group_name
        ).where(AdGroupRoleCompositeRuleGroup.rule_id.in_(rule_ids))
    )
    groups_by_rule: dict[int, list[str]] = {}
    for rule_id, ad_group_name in result.all():
        groups_by_rule.setdefault(rule_id, []).append(ad_group_name)
    return groups_by_rule


async def create_composite_rule(
    session: AsyncSession, *, role_name: str, ad_group_names: list[str], created_by: str | None
) -> dict:
    unique_groups = list(dict.fromkeys(ad_group_names))
    if len(unique_groups) < 2:
        raise TooFewGroupsError(
            "Eine Verbund-Regel braucht mindestens 2 unterschiedliche Gruppen (AND-Logik) - "
            "fuer eine einzelne Gruppe die einfache Zuordnung verwenden"
        )
    rule = AdGroupRoleCompositeRule(
        role_name=role_name, created_by=created_by, created_at=datetime.now(UTC)
    )
    session.add(rule)
    await session.flush()
    for ad_group_name in unique_groups:
        session.add(AdGroupRoleCompositeRuleGroup(rule_id=rule.id, ad_group_name=ad_group_name))
    await session.flush()
    return {
        "id": rule.id,
        "role_name": rule.role_name,
        "ad_group_names": unique_groups,
        "created_at": rule.created_at,
        "created_by": rule.created_by,
    }


async def delete_composite_rule(session: AsyncSession, rule_id: int) -> dict:
    rule = await session.get(AdGroupRoleCompositeRule, rule_id)
    if rule is None:
        raise CompositeRuleNotFoundError(f"Verbund-Regel {rule_id} nicht gefunden")
    groups_by_rule = await _groups_by_rule(session, [rule_id])
    ad_group_names = groups_by_rule.get(rule_id, [])
    result = await session.execute(
        select(AdGroupRoleCompositeRuleGroup).where(
            AdGroupRoleCompositeRuleGroup.rule_id == rule_id
        )
    )
    for row in result.scalars().all():
        await session.delete(row)
    await session.delete(rule)
    await session.flush()
    return {"id": rule_id, "role_name": rule.role_name, "ad_group_names": ad_group_names}


async def mapping_exists(session: AsyncSession, *, ad_group_name: str, role_name: str) -> bool:
    """Used by the config-service import path (7.3, Post-Roadmap Phase 39
    Session 3) to skip an already-present mapping idempotently instead of
    relying on the DB's `UniqueConstraint` raising - same reasoning as
    `POST /realm-roles`'s own `skip_exists=True`."""
    result = await session.execute(
        select(AdGroupRoleMapping.id).where(
            AdGroupRoleMapping.ad_group_name == ad_group_name,
            AdGroupRoleMapping.role_name == role_name,
        )
    )
    return result.first() is not None


async def composite_rule_exists(
    session: AsyncSession, *, role_name: str, ad_group_names: list[str]
) -> bool:
    """Same idempotency reasoning as `mapping_exists` above - matches on
    `role_name` plus the exact (unordered) set of linked groups."""
    target = set(ad_group_names)
    for rule in await list_composite_rules(session):
        if rule["role_name"] == role_name and set(rule["ad_group_names"]) == target:
            return True
    return False


async def get_default_role_config(session: AsyncSession) -> AdGroupMappingDefaultRole | None:
    return await session.get(AdGroupMappingDefaultRole, _DEFAULT_ROLE_CONFIG_ID)


async def get_default_role(session: AsyncSession) -> str | None:
    config = await get_default_role_config(session)
    return config.default_role_name if config is not None else None


async def set_default_role(
    session: AsyncSession, *, default_role_name: str | None, updated_by: str | None
) -> AdGroupMappingDefaultRole:
    config = await session.get(AdGroupMappingDefaultRole, _DEFAULT_ROLE_CONFIG_ID)
    if config is None:
        config = AdGroupMappingDefaultRole(
            id=_DEFAULT_ROLE_CONFIG_ID,
            default_role_name=default_role_name,
            updated_at=datetime.now(UTC),
            updated_by=updated_by,
        )
        session.add(config)
    else:
        config.default_role_name = default_role_name
        config.updated_at = datetime.now(UTC)
        config.updated_by = updated_by
    await session.flush()
    await session.refresh(config)
    return config


async def resolve_roles_for_groups(session: AsyncSession, groups: list[str]) -> list[str]:
    """Resolves the values of the Keycloak `groups` JWT claim (see
    `bootstrap._ensure_groups_mapper`) into internal role names - a pure
    read function, evaluated fresh against the tables on EVERY resolution
    (currently `GET /me`) rather than cached, so that a mapping/rule/
    default-role change takes effect immediately on the next call (no
    cache-invalidation problem).

    Since Post-Roadmap Phase 39 Session 3 (ADR 0153): unions two
    independent mechanisms - the plain 1:1 `AdGroupRoleMapping` table
    (OR-across-rows, unchanged since P24-S2) and `AdGroupRoleCompositeRule`
    (AND-across-a-rule's-linked-groups, new). If NEITHER mechanism matches
    anything and the principal genuinely has at least one AD group (an
    empty `groups` claim is deliberately NOT treated as "unmapped" - that
    would grant the configurable default role to every principal with no
    AD-group claim at all, not just to one whose actual groups are simply
    not mapped to anything, a materially broader and unintended blast
    radius), the configurable default role is returned instead."""
    if not groups:
        return []
    group_set = set(groups)

    simple_result = await session.execute(
        select(AdGroupRoleMapping.role_name).where(AdGroupRoleMapping.ad_group_name.in_(groups))
    )
    # `dict.fromkeys` deduplicates while preserving insertion order (e.g. a
    # principal that belongs to two groups both mapping to the same role
    # appears only once in `realm_roles`) - more deterministic than `set()`.
    matched = list(dict.fromkeys(simple_result.scalars().all()))

    composite_rules = (await session.execute(select(AdGroupRoleCompositeRule))).scalars().all()
    if composite_rules:
        groups_by_rule = await _groups_by_rule(session, [rule.id for rule in composite_rules])
        for rule in composite_rules:
            required = groups_by_rule.get(rule.id, [])
            if required and set(required).issubset(group_set) and rule.role_name not in matched:
                matched.append(rule.role_name)

    if matched:
        return matched
    default_role = await get_default_role(session)
    return [default_role] if default_role else []
