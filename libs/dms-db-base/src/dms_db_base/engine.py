import weakref

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Every engine this process built and still holds (P88-S2b): the runtime
# sensors in `dms-metrics-client` read pool usage from here on scrape,
# without each service wiring its engine into the sensor registry. Weak, so
# a disposed and dropped engine (tests build many) disappears by itself.
_live_engines: "weakref.WeakSet[AsyncEngine]" = weakref.WeakSet()


def build_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Creates the async engine for a service.

    ``dsn`` must use the ``postgresql+asyncpg://`` driver (1a). SQLAlchemy's
    pool defaults are sufficient to start with - tuning happens per service as needed.
    """
    engine = create_async_engine(dsn, echo=echo, pool_pre_ping=True)
    _live_engines.add(engine)
    return engine


def live_engines() -> list[AsyncEngine]:
    return list(_live_engines)
