from dms_metrics_client import SensorRegistry, SensorSpec, metrics_payload


def _spec(name: str, group: str = "test", cost: str = "cheap") -> SensorSpec:
    return SensorSpec(name=name, group=group, cost=cost, description="Testsensor")


def test_counter_records_when_active():
    registry = SensorRegistry("test-service", is_active=lambda name: True)
    counter = registry.counter(_spec("test.counter"))
    counter.inc()
    counter.inc(2)
    body, _ = metrics_payload(registry)
    # prometheus_client haengt Countern automatisch ein "_total"-Suffix an.
    assert b"test_counter_total 3.0" in body


def test_counter_does_not_record_when_inactive():
    registry = SensorRegistry("test-service", is_active=lambda name: False)
    counter = registry.counter(_spec("test.counter"))
    counter.inc()
    body, _ = metrics_payload(registry)
    # Keine Erfassung bei Deaktivierung (10.1) - nicht nur unsichtbar, der
    # Wert wurde nie inkrementiert.
    assert b"test_counter_total 0.0" in body


def test_gauge_set_guarded_by_active_flag():
    active = {"value": True}
    registry = SensorRegistry("test-service", is_active=lambda name: active["value"])
    gauge = registry.gauge(_spec("test.gauge"))

    gauge.set(42)
    body, _ = metrics_payload(registry)
    assert b"test_gauge 42.0" in body

    active["value"] = False
    gauge.set(99)
    body, _ = metrics_payload(registry)
    # Deaktiviert: der neue Wert 99 wurde nie gesetzt, der alte 42 bleibt stehen.
    assert b"test_gauge 42.0" in body
    assert b"test_gauge 99.0" not in body


def test_histogram_is_active_lets_caller_skip_timing():
    registry = SensorRegistry("test-service", is_active=lambda name: False)
    histogram = registry.histogram(_spec("test.duration", cost="expensive"))
    assert histogram.is_active() is False
    histogram.observe(1.23)
    body, _ = metrics_payload(registry)
    assert b"test_duration_sum 1.23" not in body


def test_duplicate_sensor_name_raises():
    registry = SensorRegistry("test-service", is_active=lambda name: True)
    registry.counter(_spec("test.dup"))
    try:
        registry.gauge(_spec("test.dup"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for duplicate sensor name")


def test_specs_lists_all_registered_sensors():
    registry = SensorRegistry("test-service", is_active=lambda name: True)
    registry.counter(_spec("test.a"))
    registry.gauge(_spec("test.b", group="capacity", cost="expensive"))
    names = {spec.name for spec in registry.specs()}
    assert names == {"test.a", "test.b"}
