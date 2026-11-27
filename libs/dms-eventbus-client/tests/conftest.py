"""Shared fixtures for the eventbus library tests."""

import os
import re

import nats
import pytest

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
_TEST_STREAM = re.compile(r"^test_[0-9a-f]{8}$")


@pytest.fixture(autouse=True)
async def _remove_test_streams_and_replay_consumers():
    """Every test stream (`test_<hex>`) and its replay consumers go after the
    test (P99-S2). Durables outlive their process by design; left alone,
    67 test streams had piled up on the dev stack by P99."""
    yield
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    try:
        for stream in await js.streams_info():
            if _TEST_STREAM.match(stream.config.name):
                await js.delete_stream(stream.config.name)
        try:
            for consumer in await js.consumers_info("REPLAY"):
                if (consumer.config.filter_subject or "").startswith("replay.test_"):
                    await js.delete_consumer("REPLAY", consumer.name)
            await js.purge_stream("REPLAY", subject="replay.test_*.>")
        except Exception:  # noqa: BLE001 - no REPLAY stream yet
            pass
    finally:
        await nc.close()
