"""AD-/Keycloak-Gruppe -> interne Rolle-Mapping (4.4, P24-S2).

Reine Datenzugriffs-/Auflösungsfunktionen für `models.AdGroupRoleMapping` -
`main.py` verdrahtet daraus die Admin-CRUD-Endpunkte (`/ad-group-mappings`)
und den Aufruf in `GET /me`. Bewusster Scope-Cut dieser Session: nur
einfache 1:1-Zuordnung (eine `ad_group_name` -> eine `role_name`), siehe
`models.AdGroupRoleMapping`-Docstring und ADR 0093.
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
    """Löst die Werte des Keycloak-`groups`-JWT-Claims (siehe
    `bootstrap._ensure_groups_mapper`) in interne Rollennamen auf - reine
    Lesefunktion, bei JEDER Auflösung (aktuell `GET /me`) frisch gegen die
    Tabelle ausgewertet statt zwischengespeichert, damit eine Änderung/ein
    Löschen einer Zuordnung sich sofort beim nächsten Aufruf auswirkt (kein
    Caching-Invalidierungsproblem). Ein Principal ohne gemappte Gruppen
    (leere `groups`-Liste oder keine Übereinstimmung) bekommt eine leere
    Liste zurück, bleibt also unverändert."""
    if not groups:
        return []
    result = await session.execute(
        select(AdGroupRoleMapping.role_name).where(AdGroupRoleMapping.ad_group_name.in_(groups))
    )
    # `dict.fromkeys` dedupliziert bei stabiler Einfügereihenfolge (z. B. ist
    # ein Principal in zwei Gruppen, die beide auf dieselbe Rolle mappen, nur
    # einmal in `realm_roles` vertreten) - deterministischer als `set()`.
    return list(dict.fromkeys(result.scalars().all()))
