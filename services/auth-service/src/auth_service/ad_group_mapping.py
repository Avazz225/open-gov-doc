"""AD/Keycloak group -> internal role mapping (4.4, P24-S2).

Pure data-access/resolution functions for `models.AdGroupRoleMapping` -
`main.py` wires these up into the admin CRUD endpoints
(`/ad-group-mappings`) and the call in `GET /me`. Deliberate scope cut for
this session: only simple 1:1 mapping (one `ad_group_name` -> one
`role_name`), see the `models.AdGroupRoleMapping` docstring and ADR 0093.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service.models import AdGroupRoleMapping


class MappingNotFoundError(Exception):
    pass


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


async def resolve_roles_for_groups(session: AsyncSession, groups: list[str]) -> list[str]:
    """Resolves the values of the Keycloak `groups` JWT claim (see
    `bootstrap._ensure_groups_mapper`) into internal role names - a pure
    read function, evaluated fresh against the table on EVERY resolution
    (currently `GET /me`) rather than cached, so that a change to or
    deletion of a mapping takes effect immediately on the next call (no
    cache-invalidation problem). A principal with no mapped groups (empty
    `groups` list or no match) gets an empty list back, and thus remains
    unchanged."""
    if not groups:
        return []
    result = await session.execute(
        select(AdGroupRoleMapping.role_name).where(AdGroupRoleMapping.ad_group_name.in_(groups))
    )
    # `dict.fromkeys` deduplicates while preserving insertion order (e.g. a
    # principal that belongs to two groups both mapping to the same role
    # appears only once in `realm_roles`) - more deterministic than `set()`.
    return list(dict.fromkeys(result.scalars().all()))
