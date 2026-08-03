from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import repository
from storage_service.settings import BackendTargetConfig, Settings


def has_governance_bypass_role(x_dms_roles: str, settings: Settings) -> bool:
    """Serverseitige Rollen-Header-Prüfung (5.1/5.2a, seit P7-S1) - gleiches
    Muster wie `document_service.kennzeichen_admin_role`/
    `_has_kennzeichen_admin_role` (main.py): der Gateway reicht `X-DMS-Roles`
    bereits seit P4-S1 als kommagetrennte Liste durch."""
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.governance_bypass_role in roles


async def find_locked_targets(
    session: AsyncSession, key: str, *, targets: list[BackendTargetConfig]
) -> list[str]:
    """Liefert die Ziel-`id`s, deren Kopie dieses Objekts noch unter einer in
    der Zukunft liegenden Aufbewahrungsfrist steht UND deren Ziel-Konfiguration
    `object_lock_mode="governance"` gesetzt hat (5.1/5.2a). Ein Ziel mit
    gesetztem `retention_until`, aber OHNE `object_lock_mode`, blockiert die
    Löschung bewusst NICHT - Governance-Mode ist eine gezielt aktivierte
    Ausnahme, kein globaler Zwang für jedes konfigurierte Ziel."""
    lock_mode_by_id = {t.id: t.object_lock_mode for t in targets}
    now = datetime.now(UTC)
    blocked: list[str] = []
    for copy in await repository.list_copies(session, key):
        if copy.retention_until is None or copy.retention_until <= now:
            continue
        if lock_mode_by_id.get(copy.backend_id) == "governance":
            blocked.append(copy.backend_id)
    return blocked
