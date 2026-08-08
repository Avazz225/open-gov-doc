from monitoring_service import repository
from monitoring_service.clients import RegistryInstance


def make_instance(service_type: str, sensors: list[dict]) -> RegistryInstance:
    return RegistryInstance(
        instance_id=f"{service_type}-1",
        service_type=service_type,
        address="http://x:8000",
        sensors=sensors,
    )


async def test_get_sensor_config_defaults_to_all_active(session):
    config = await repository.get_sensor_config(session)
    assert config.global_default is True
    assert config.overrides == {}


async def test_set_global_default_persists(session):
    await repository.set_global_default(session, False)
    config = await repository.get_sensor_config(session)
    assert config.global_default is False


async def test_set_sensor_override_takes_precedence_over_global(session):
    await repository.set_global_default(session, True)
    await repository.set_sensor_override(session, "some.sensor", False)
    config = await repository.get_sensor_config(session)
    assert repository.is_sensor_active(config, "some.sensor") is False
    assert repository.is_sensor_active(config, "other.sensor") is True


async def test_clearing_override_falls_back_to_global_default(session):
    await repository.set_global_default(session, True)
    await repository.set_sensor_override(session, "some.sensor", False)
    await repository.set_sensor_override(session, "some.sensor", None)
    config = await repository.get_sensor_config(session)
    assert "some.sensor" not in config.overrides
    assert repository.is_sensor_active(config, "some.sensor") is True


async def test_ensure_global_default_seeded_is_idempotent(session):
    await repository.ensure_global_default_seeded(session)
    await repository.ensure_global_default_seeded(session)
    config = await repository.get_sensor_config(session)
    assert config.global_default is True


async def test_list_sensors_aggregates_across_instances_and_resolves_active(session):
    await repository.set_global_default(session, True)
    await repository.set_sensor_override(session, "document.upload.duration", False)
    instances = [
        make_instance(
            "registry-service",
            [
                {
                    "name": "registry.instances.active_total",
                    "group": "capacity",
                    "cost": "cheap",
                    "description": "x",
                }
            ],
        ),
        make_instance(
            "document-service",
            [
                {
                    "name": "document.upload.duration",
                    "group": "performance",
                    "cost": "expensive",
                    "description": "y",
                }
            ],
        ),
    ]
    sensors = await repository.list_sensors(session, instances)
    by_name = {s.name: s for s in sensors}
    assert by_name["registry.instances.active_total"].active is True
    assert by_name["registry.instances.active_total"].service_types == ["registry-service"]
    assert by_name["document.upload.duration"].active is False
