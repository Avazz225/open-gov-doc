from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def build_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Creates the async engine for a service.

    ``dsn`` must use the ``postgresql+asyncpg://`` driver (1a). SQLAlchemy's
    pool defaults are sufficient to start with - tuning happens per service as needed.
    """
    return create_async_engine(dsn, echo=echo, pool_pre_ping=True)
