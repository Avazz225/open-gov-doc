from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "favorite-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    monitoring_service_base_url: str = "http://localhost:8026"

    # Follow-up reminders (P90-S1, ADR 0246): how often due reminders are
    # looked for. One minute is the resolution of a reminder's due time in
    # practice; the poll is a single indexed query.
    reminder_poll_interval_seconds: float = 60.0
    # The first poll waits a moment, so tests can replace clients first (same
    # reason as archival-service's `archival_poll_initial_delay_seconds`).
    reminder_poll_initial_delay_seconds: float = 2.0
