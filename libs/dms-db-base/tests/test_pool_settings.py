"""Pool sizing from the environment (P89-S4, ADR 0254) - no database needed."""

import pytest
from dms_db_base import build_engine, pool_settings
from dms_db_base.engine import DEFAULT_MAX_OVERFLOW, DEFAULT_POOL_SIZE


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("DMS_DB_POOL_SIZE", "DMS_DB_MAX_OVERFLOW", "DMS_DB_POOL_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_the_small_budget():
    assert pool_settings() == {
        "pool_size": DEFAULT_POOL_SIZE,
        "max_overflow": DEFAULT_MAX_OVERFLOW,
        "pool_timeout": 30,
    }
    assert DEFAULT_POOL_SIZE + DEFAULT_MAX_OVERFLOW == 5


def test_environment_overrides_and_blank_means_default(monkeypatch):
    monkeypatch.setenv("DMS_DB_POOL_SIZE", "10")
    monkeypatch.setenv("DMS_DB_MAX_OVERFLOW", "20")
    monkeypatch.setenv("DMS_DB_POOL_TIMEOUT_SECONDS", " ")
    assert pool_settings() == {"pool_size": 10, "max_overflow": 20, "pool_timeout": 30}


def test_invalid_values_fail_loudly(monkeypatch):
    monkeypatch.setenv("DMS_DB_POOL_SIZE", "-1")
    with pytest.raises(ValueError, match="DMS_DB_POOL_SIZE"):
        pool_settings()
    monkeypatch.setenv("DMS_DB_POOL_SIZE", "viele")
    with pytest.raises(ValueError):
        pool_settings()


async def test_the_engine_pool_uses_them(monkeypatch):
    """No connection is opened: building the engine only configures the pool."""
    monkeypatch.setenv("DMS_DB_POOL_SIZE", "7")
    monkeypatch.setenv("DMS_DB_MAX_OVERFLOW", "4")
    engine = build_engine("postgresql+asyncpg://u:p@localhost:1/none")
    try:
        pool = engine.pool
        assert (pool.size(), pool._max_overflow) == (7, 4)
    finally:
        await engine.dispose()
