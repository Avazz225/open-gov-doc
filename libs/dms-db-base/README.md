# dms-db-base

Async-SQLAlchemy-Basis, die die Schema-pro-Service-Konvention (Konzept 3.1) durchsetzt.

- `build_engine(dsn)` — Async-Engine (`postgresql+asyncpg://`).
- `make_declarative_base(schema)` — Declarative-Base, deren Metadata fest an ein Postgres-Schema gebunden ist. Jeder Service ruft dies **einmal** mit seinem eigenen Schemanamen auf.
- `make_session_factory(engine)` / `session_scope(factory)` — Session-Erzeugung inkl. Commit-oder-Rollback-Block.

## Nutzung

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

Integrationstest gegen echtes Postgres (nutzt `infra/docker-compose.yml`):

```bash
cd infra && docker compose up -d postgres && cd ..
uv run pytest libs/dms-db-base/tests
```

`TEST_POSTGRES_DSN` überschreibt die DSN bei Bedarf (Default passt zu `infra/.env.example`).
