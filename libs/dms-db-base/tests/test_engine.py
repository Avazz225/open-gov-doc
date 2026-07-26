import os
import uuid

import pytest
from dms_db_base import build_engine, make_declarative_base, make_session_factory, session_scope
from sqlalchemy import String, select, text
from sqlalchemy.orm import Mapped, mapped_column

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)


@pytest.fixture
async def schema_name():
    name = f"test_{uuid.uuid4().hex[:8]}"
    engine = build_engine(DSN)
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {name}"))
    yield name
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA {name} CASCADE"))
    await engine.dispose()


async def test_roundtrip_through_own_schema(schema_name):
    Base = make_declarative_base(schema_name)

    class Widget(Base):
        __tablename__ = "widget"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(100))

    engine = build_engine(DSN)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = make_session_factory(engine)
    async with session_scope(factory) as session:
        session.add(Widget(id=1, name="first"))

    async with factory() as session:
        result = await session.execute(select(Widget).where(Widget.id == 1))
        widget = result.scalar_one()
        assert widget.name == "first"

    await engine.dispose()
