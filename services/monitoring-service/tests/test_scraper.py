import httpx
from monitoring_service.clients import RegistryInstance
from monitoring_service.scraper import merge_metric_families, scrape_targets


def test_merge_metric_families_adds_instance_label_and_groups_by_name():
    bodies = {
        "registry-service-1": (
            "# HELP registry_instances_active_total help\n"
            "# TYPE registry_instances_active_total gauge\n"
            "registry_instances_active_total 3.0\n"
        ),
        "registry-service-2": (
            "# HELP registry_instances_active_total help\n"
            "# TYPE registry_instances_active_total gauge\n"
            "registry_instances_active_total 5.0\n"
        ),
    }
    service_types = {
        "registry-service-1": "registry-service",
        "registry-service-2": "registry-service",
    }
    families = merge_metric_families(bodies, service_types)
    assert len(families) == 1
    family = families[0]
    assert family.name == "registry_instances_active_total"
    values_by_instance = {s.labels["instance"]: s.value for s in family.samples}
    assert values_by_instance == {"registry-service-1": 3.0, "registry-service-2": 5.0}
    assert all(s.labels["service"] == "registry-service" for s in family.samples)


def test_merge_metric_families_defaults_service_label_to_unknown_when_not_registered():
    bodies = {"orphan-instance": "# TYPE a_total counter\na_total 1.0\n"}
    families = merge_metric_families(bodies, service_types={})
    assert families[0].samples[0].labels["service"] == "unknown"


def test_merge_metric_families_keeps_distinct_metric_names_separate():
    bodies = {
        "a": "# HELP a_total help\n# TYPE a_total counter\na_total 1.0\n",
        "b": "# HELP b_total help\n# TYPE b_total counter\nb_total 2.0\n",
    }
    families = merge_metric_families(bodies, service_types={})
    # Der Parser normalisiert Counter-Familiennamen ohne "_total"-Suffix.
    assert {f.name for f in families} == {"a", "b"}


async def test_scrape_targets_skips_failing_target_and_reports_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if "good" in str(request.url):
            return httpx.Response(200, text="# TYPE ok_total counter\nok_total 1.0\n")
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    instances = [
        RegistryInstance("good-1", "good-service", "http://good", []),
        RegistryInstance("bad-1", "bad-service", "http://bad", []),
    ]
    failures = {"count": 0}
    bodies = await scrape_targets(
        client,
        instances,
        timeout_seconds=1.0,
        on_failure=lambda: failures.__setitem__("count", failures["count"] + 1),
    )
    assert set(bodies.keys()) == {"good-1"}
    assert failures["count"] == 1
