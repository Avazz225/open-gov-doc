from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import repository
from storage_service.settings import BackendTargetConfig, Settings


def has_governance_bypass_role(x_dms_roles: str, settings: Settings) -> bool:
    """Server-side role-header check (5.1/5.2a, since P7-S1) - same
    pattern as `document_service.kennzeichen_admin_role`/
    `_has_kennzeichen_admin_role` (main.py): the gateway has been passing
    through `X-DMS-Roles` as a comma-separated list since P4-S1."""
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.governance_bypass_role in roles


async def find_locked_targets(
    session: AsyncSession, key: str, *, targets: list[BackendTargetConfig]
) -> list[str]:
    """Returns the target `id`s whose copy of this object is still under a
    retention period that lies in the future AND whose target
    configuration has `object_lock_mode="governance"` set (5.1/5.2a). A
    target with `retention_until` set but WITHOUT `object_lock_mode`
    deliberately does NOT block deletion - Governance mode is a
    deliberately activated exception, not a global requirement for every
    configured target."""
    lock_mode_by_id = {t.id: t.object_lock_mode for t in targets}
    now = datetime.now(UTC)
    blocked: list[str] = []
    for copy in await repository.list_copies(session, key):
        if copy.retention_until is None or copy.retention_until <= now:
            continue
        if lock_mode_by_id.get(copy.backend_id) == "governance":
            blocked.append(copy.backend_id)
    return blocked
