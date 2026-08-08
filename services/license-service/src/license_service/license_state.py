from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from license_service.models import INSTALLED_LICENSE_ID, InstalledLicense


async def get_installed(session: AsyncSession) -> InstalledLicense | None:
    return await session.get(InstalledLicense, INSTALLED_LICENSE_ID)


async def install(
    session: AsyncSession,
    *,
    raw_token: str,
    installed_by: str,
    issued_at: datetime | None,
    expires_at: datetime | None,
) -> InstalledLicense:
    license_row = await session.get(InstalledLicense, INSTALLED_LICENSE_ID)
    if license_row is None:
        license_row = InstalledLicense(id=INSTALLED_LICENSE_ID)
        session.add(license_row)
    license_row.raw_token = raw_token
    license_row.installed_by = installed_by
    license_row.installed_at = datetime.now(UTC)
    license_row.issued_at = issued_at
    license_row.expires_at = expires_at
    # Neuinstallation setzt die Flankenerkennung zurueck (siehe poll_loop.py)
    # - eine neue Lizenz verdient eine frische Bewertung, keine geerbten
    # "bereits gemeldet"-Flags der vorherigen Lizenz.
    license_row.last_status_snapshot = {}
    return license_row
