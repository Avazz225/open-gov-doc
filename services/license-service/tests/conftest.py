import os
import sys
from pathlib import Path

import pytest
from dms_db_base import build_engine, make_session_factory
from license_service.models import Base
from sqlalchemy import text

# `--import-mode=importlib` (workspace-weit) kennt keine `tests/__init__.py` -
# `tests/fixtures` wird auf `sys.path` gelegt. Modul-Ebene statt Fixture: Test-
# Module importieren `license_factory` bereits beim Sammeln (vor jedem
# Fixture-Lauf), gleiches Muster wie query-service's `fake_parser_plugin`,
# hier aber als direkter `from license_factory import ...`-Import statt
# dynamischem `importlib`-Laden.
FIXTURES_DIR = Path(__file__).parent / "fixtures"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))


# P98-S2 (ADR 0266 follow-up): a throwaway signing key per test session
# instead of the git-ignored `fixtures/dev_private_key.pem`, which exists only
# on developer machines - a fresh clone couldn't run these tests. Set at
# module level, before any test module imports `license_factory` or the app
# reads its settings: the app verifies against this public key, and
# `license_factory` signs with the private half.
def _throwaway_key_pair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    return private_pem, public_pem


_TEST_PRIVATE_PEM, _TEST_PUBLIC_PEM = _throwaway_key_pair()
os.environ["TEST_LICENSE_PRIVATE_KEY_PEM"] = _TEST_PRIVATE_PEM
os.environ["DMS_LICENSE_PUBLIC_KEY_PEM"] = _TEST_PUBLIC_PEM


DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
os.environ["DMS_POSTGRES_DSN"] = DSN


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS license"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE license.installed_license"))
        await conn.execute(text("TRUNCATE license.license_display_config"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS license"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
