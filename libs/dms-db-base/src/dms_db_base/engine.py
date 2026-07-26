from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def build_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Erstellt die Async-Engine für einen Service.

    ``dsn`` muss den ``postgresql+asyncpg://``-Treiber verwenden (1a). Pool-Defaults
    von SQLAlchemy sind für den Start ausreichend - Tuning erfolgt bei Bedarf pro Service.
    """
    return create_async_engine(dsn, echo=echo, pool_pre_ping=True)
