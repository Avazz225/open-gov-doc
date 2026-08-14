# dms-db-base

Async SQLAlchemy base that enforces the schema-per-service convention (Concept 3.1).

- `build_engine(dsn)` — async engine (`postgresql+asyncpg://`).
- `make_declarative_base(schema)` — declarative base whose metadata is fixed to a Postgres schema. Every service calls this **once** with its own schema name.
- `make_session_factory(engine)` / `session_scope(factory)` — session creation including a commit-or-rollback block.

## Usage

```python
from dms_db_base import build_engine, make_declarative_base, make_session_factory, session_scope

Base = make_declarative_base("registry")


class ServiceInstance(Base):
    __tablename__ = "service_instance"
    ...


engine = build_engine(settings.postgres_dsn)
factory = make_session_factory(engine)

async with session_scope(factory) as session:
    session.add(ServiceInstance(...))
```

## Tests

Integration test against real Postgres (uses `infra/docker-compose.yml`):

```bash
cd infra && docker compose up -d postgres && cd ..
uv run pytest libs/dms-db-base/tests
```

`TEST_POSTGRES_DSN` overrides the DSN if needed (default matches `infra/.env.example`).
