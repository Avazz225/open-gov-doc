import os
import weakref

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Every engine this process built and still holds (P88-S2b): the runtime
# sensors in `dms-metrics-client` read pool usage from here on scrape,
# without each service wiring its engine into the sensor registry. Weak, so
# a disposed and dropped engine (tests build many) disappears by itself.
_live_engines: "weakref.WeakSet[AsyncEngine]" = weakref.WeakSet()


# Connection budget (P89-S4, ADR 0254). A pool never closes idle connections
# by itself, so every process can hold up to `pool_size + max_overflow`
# connections - with about 30 database-backed services that has to fit into
# Postgres' `max_connections`. SQLAlchemy's own defaults (5 + 10 = 15 per
# process) did not: the load test ran Postgres out of connections. Small
# defaults here; the few services that serve every request (permission,
# document, folder, storage) get more through the environment (compose and
# the Helm values). A busy pool makes a request wait (`DMS_DB_POOL_TIMEOUT_
# SECONDS`) instead of Postgres refusing it outright.
DEFAULT_POOL_SIZE = 2
DEFAULT_MAX_OVERFLOW = 3
DEFAULT_POOL_TIMEOUT_SECONDS = 30


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def pool_settings() -> dict[str, int]:
    """The pool arguments from `DMS_DB_POOL_SIZE`, `DMS_DB_MAX_OVERFLOW` and
    `DMS_DB_POOL_TIMEOUT_SECONDS`, falling back to the budget defaults."""
    return {
        "pool_size": _env_int("DMS_DB_POOL_SIZE", DEFAULT_POOL_SIZE),
        "max_overflow": _env_int("DMS_DB_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW),
        "pool_timeout": _env_int("DMS_DB_POOL_TIMEOUT_SECONDS", DEFAULT_POOL_TIMEOUT_SECONDS),
    }


def build_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Creates the async engine for a service.

    ``dsn`` must use the ``postgresql+asyncpg://`` driver (1a). The pool is
    sized by `pool_settings()` (P89-S4) - see the budget note above.
    """
    engine = create_async_engine(dsn, echo=echo, pool_pre_ping=True, **pool_settings())
    _live_engines.add(engine)
    return engine


def live_engines() -> list[AsyncEngine]:
    return list(_live_engines)
