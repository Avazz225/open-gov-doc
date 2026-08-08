import asyncio
import os
import uuid

import httpx
import pytest
from dms_registry_client import RegistryRegistration, maybe_start_registration

REGISTRY_URL = os.environ.get("TEST_REGISTRY_SERVICE_URL", "http://localhost:8001")


async def _get_instance(service_type: str, instance_id: str) -> dict | None:
    async with httpx.AsyncClient(base_url=REGISTRY_URL) as client:
        response = await client.get(f"/instances/{service_type}")
        response.raise_for_status()
        return next((i for i in response.json() if i["instance_id"] == instance_id), None)


async def _deregister_via_api(instance_id: str) -> None:
    async with httpx.AsyncClient(base_url=REGISTRY_URL) as client:
        await client.delete(f"/instances/{instance_id}")


def _service_type() -> str:
    return f"test-service-{uuid.uuid4().hex[:8]}"


async def test_start_registers_instance_as_healthy():
    service_type = _service_type()
    registration = RegistryRegistration(
        registry_base_url=REGISTRY_URL,
        service_type=service_type,
        version="0.1.0",
        address="http://localhost:9999",
        heartbeat_interval_seconds=60,
    )
    await registration.start()
    try:
        instance = await _get_instance(service_type, registration.instance_id)
        assert instance is not None
        assert instance["healthy"] is True
        assert instance["address"] == "http://localhost:9999"
    finally:
        await registration.stop()


async def test_heartbeat_loop_keeps_instance_alive():
    service_type = _service_type()
    registration = RegistryRegistration(
        registry_base_url=REGISTRY_URL,
        service_type=service_type,
        version="0.1.0",
        address="http://localhost:9999",
        heartbeat_interval_seconds=0.1,
    )
    await registration.start()
    try:
        await asyncio.sleep(0.35)
        instance = await _get_instance(service_type, registration.instance_id)
        assert instance is not None
        assert instance["healthy"] is True
    finally:
        await registration.stop()


async def test_heartbeat_reregisters_after_registry_forgot_instance():
    """Deckt den Fall ab, der beim ersten Docker-Compose-Smoketest von P4-S1
    auftrat: object-type-service startete, bevor die Registry erreichbar war,
    die allererste Registrierung schlug fehl, und der Service heartbeatete
    danach für immer gegen eine nie existierende Instanz (404), ohne sich je
    erneut zu registrieren. Simuliert hier durch manuelles Entfernen der
    Instanz zwischen zwei Heartbeats - der nächste Heartbeat muss die
    Registrierung selbst heilen.
    """
    service_type = _service_type()
    registration = RegistryRegistration(
        registry_base_url=REGISTRY_URL,
        service_type=service_type,
        version="0.1.0",
        address="http://localhost:9999",
        heartbeat_interval_seconds=0.1,
    )
    await registration.start()
    try:
        await _deregister_via_api(registration.instance_id)
        assert await _get_instance(service_type, registration.instance_id) is None

        await asyncio.sleep(0.35)

        instance = await _get_instance(service_type, registration.instance_id)
        assert instance is not None
        assert instance["healthy"] is True
    finally:
        await registration.stop()


async def test_stop_deregisters_instance():
    service_type = _service_type()
    registration = RegistryRegistration(
        registry_base_url=REGISTRY_URL,
        service_type=service_type,
        version="0.1.0",
        address="http://localhost:9999",
        heartbeat_interval_seconds=60,
    )
    await registration.start()
    instance_id = registration.instance_id
    await registration.stop()

    instance = await _get_instance(service_type, instance_id)
    assert instance is None


async def test_start_registers_sensor_declarations():
    service_type = _service_type()
    registration = RegistryRegistration(
        registry_base_url=REGISTRY_URL,
        service_type=service_type,
        version="0.1.0",
        address="http://localhost:9999",
        heartbeat_interval_seconds=60,
        sensors=[{"name": "test.sensor", "group": "test", "cost": "cheap", "description": "x"}],
    )
    await registration.start()
    try:
        instance = await _get_instance(service_type, registration.instance_id)
        assert instance is not None
        assert instance["sensors"] == [
            {"name": "test.sensor", "group": "test", "cost": "cheap", "description": "x"}
        ]
    finally:
        await registration.stop()


async def test_maybe_start_registration_returns_none_without_config():
    registration = await maybe_start_registration(
        registry_service_base_url=None,
        self_address="http://localhost:9999",
        service_type=_service_type(),
        version="0.1.0",
    )
    assert registration is None


async def test_maybe_start_registration_registers_when_configured():
    service_type = _service_type()
    registration = await maybe_start_registration(
        registry_service_base_url=REGISTRY_URL,
        self_address="http://localhost:9999",
        service_type=service_type,
        version="0.1.0",
        heartbeat_interval_seconds=60,
    )
    assert registration is not None
    try:
        instance = await _get_instance(service_type, registration.instance_id)
        assert instance is not None
    finally:
        await registration.stop()


@pytest.mark.parametrize("bad_url", ["http://localhost:1"])
async def test_unreachable_registry_does_not_raise(bad_url):
    registration = RegistryRegistration(
        registry_base_url=bad_url,
        service_type=_service_type(),
        version="0.1.0",
        address="http://localhost:9999",
        heartbeat_interval_seconds=60,
    )
    await registration.start()
    await registration.stop()
