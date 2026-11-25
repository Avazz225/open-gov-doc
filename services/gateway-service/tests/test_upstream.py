import httpx
import pytest
from gateway_service.upstream import InstanceResolver, filter_headers
from starlette.datastructures import Headers


def _make_resolver() -> InstanceResolver:
    # `pick()`/`reserve()`/`release()`/`reserved_instance()` greifen nie auf
    # `_client`/die Registry zu - ein echter `httpx.AsyncClient` ohne
    # tatsächliche Requests reicht hier aus, kein Registry-Mock nötig.
    return InstanceResolver(
        client=httpx.AsyncClient(), registry_base_url="http://registry.test", cache_ttl_seconds=5.0
    )


def test_filter_headers_drops_hop_by_hop_headers():
    headers = Headers(raw=[(b"connection", b"keep-alive"), (b"content-type", b"application/json")])
    assert filter_headers(headers) == {"content-type": "application/json"}


def test_filter_headers_drops_client_supplied_x_dms_headers():
    """Sicherheitsfund (P14-S11-Live-Verifikation, siehe ADR 0049): ein Client
    darf keinen eigenen `X-DMS-*`-Header mitschicken, der neben dem später
    von `proxy()` injizierten echten Header bestehen bleibt - Python-Dict-
    Keys sind case-sensitiv, ein `x-dms-principal` (ASGI-normalisiert,
    lowercase) und ein `X-DMS-Principal` (aus `identity_headers`) sind zwei
    verschiedene Schlüssel, wenn dieser Filter ihn nicht vorher entfernt."""
    headers = Headers(
        raw=[
            (b"x-dms-principal", b"attacker-spoofed-id"),
            (b"x-dms-roles", b"dms-admin"),
            (b"x-dms-maintenance-active", b"false"),
            (b"authorization", b"Bearer real-token"),
        ]
    )
    result = filter_headers(headers)
    assert "x-dms-principal" not in result
    assert "x-dms-roles" not in result
    assert "x-dms-maintenance-active" not in result
    assert result == {"authorization": "Bearer real-token"}


def test_filter_headers_case_insensitive_for_x_dms_prefix():
    headers = Headers(raw=[(b"X-Dms-Principal", b"spoofed")])
    assert filter_headers(headers) == {}


# --- Workload-bewusste Instanzauswahl (P25-S4) ---------------------------


def test_pick_selects_instance_with_fewest_open_requests():
    """Kern der P25-S4-Umstellung (ADR 0005: vorher `random.choice`): unter
    mehreren Instanzen mit unterschiedlicher aktueller Last muss `pick()`
    deterministisch die am wenigsten ausgelastete wählen, nicht zufällig
    irgendeine."""
    resolver = _make_resolver()
    busy = {"address": "http://busy.test"}
    idle = {"address": "http://idle.test"}
    also_busy = {"address": "http://also-busy.test"}
    instances = [busy, idle, also_busy]

    # busy bekommt zwei offene Reservierungen, also_busy eine, idle keine -
    # idle muss trotz Listenreihenfolge (nicht zuerst) gewinnen.
    resolver.reserve(busy)
    resolver.reserve(busy)
    resolver.reserve(also_busy)

    for _ in range(10):
        assert resolver.pick(instances) == idle


def test_pick_ties_break_among_the_minimum_not_always_first_in_list():
    """Tie-Break-Entscheidung (siehe `pick()`-Docstring): bei gleichauf
    liegenden Instanzen (hier: alle bei 0 offenen Anfragen, Startzustand)
    wird zufällig unter den Minimum-Kandidaten gewählt, nicht immer die
    erste Instanz der Liste - sonst würde die erste Instanz systematisch
    bevorzugt, bis sie als Erste einen offenen Request hätte."""
    resolver = _make_resolver()
    instances = [{"address": f"http://instance-{i}.test"} for i in range(5)]

    picked_addresses = {resolver.pick(instances)["address"] for _ in range(200)}

    assert picked_addresses == {i["address"] for i in instances}


def test_reserve_release_cycle_returns_counter_to_prior_state():
    """Ein vollständiger reserve()->release()-Zyklus darf den internen
    Zähler nicht dauerhaft verändern - sonst würde eine Instanz nach genug
    abgeschlossenen Requests fälschlich als dauerhaft ausgelastet gelten."""
    resolver = _make_resolver()
    instance = {"address": "http://instance.test"}
    other = {"address": "http://other.test"}
    instances = [instance, other]

    # Vor jeder Reservierung: beide gleich (0), Auswahl ist Zufall zwischen
    # beiden - nach reserve()/release() muss das wieder exakt so sein.
    resolver.reserve(instance)
    assert resolver._open_requests[instance["address"]] == 1
    resolver.release(instance)
    assert resolver._open_requests.get(instance["address"], 0) == 0

    # Nach dem vollen Zyklus sind beide Instanzen wieder gleich ausgelastet -
    # `pick()` muss über viele Versuche beide auswählen, nicht nur `other`.
    picked_addresses = {resolver.pick(instances)["address"] for _ in range(200)}
    assert picked_addresses == {instance["address"], other["address"]}


async def test_reserved_instance_releases_on_success():
    resolver = _make_resolver()
    instance = {"address": "http://instance.test"}

    async with resolver.reserved_instance([instance]) as picked:
        assert picked == instance
        assert resolver._open_requests[instance["address"]] == 1

    assert resolver._open_requests[instance["address"]] == 0


async def test_reserved_instance_releases_when_upstream_call_raises():
    """Der wahrscheinlichste Ort für ein stilles Leak: schlägt der
    eigentliche Upstream-Aufruf innerhalb des `with`-Blocks mit einer
    Exception fehl (hier: simulierter `httpx.HTTPError`, wie ihn `proxy()`
    in main.py aus einem tatsächlichen Verbindungsfehler bekäme), MUSS der
    reservierte Slot trotzdem freigegeben werden - sonst gilt diese Instanz
    ab dem ersten Fehler dauerhaft fälschlich als ausgelastet und wird nie
    wieder von `pick()` bevorzugt."""
    resolver = _make_resolver()
    instance = {"address": "http://instance.test"}

    with pytest.raises(httpx.HTTPError):
        async with resolver.reserved_instance([instance]) as picked:
            assert picked == instance
            assert resolver._open_requests[instance["address"]] == 1
            raise httpx.HTTPError("simulierter Verbindungsfehler")

    assert resolver._open_requests[instance["address"]] == 0


# --- P95-S1a (ADR 0262): single-flight and bulkhead --------------------------

import asyncio  # noqa: E402
import time  # noqa: E402

from gateway_service.upstream import (  # noqa: E402
    MaintenanceStateClient,
    UpstreamBulkhead,
    UpstreamBusyError,
    parse_limits,
)


def _counting_transport(calls: list[str], payload, delay: float = 0.05):
    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        await asyncio.sleep(delay)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


async def test_resolver_fetches_once_per_expiry_under_concurrency():
    calls: list[str] = []
    resolver = InstanceResolver(
        client=httpx.AsyncClient(
            transport=_counting_transport(
                calls, [{"address": "http://a:8000", "healthy": True, "status": "active"}]
            )
        ),
        registry_base_url="http://registry.test",
        cache_ttl_seconds=5.0,
    )

    results = await asyncio.gather(*(resolver.resolve("folder-service") for _ in range(50)))

    assert calls == ["/instances/folder-service"]
    assert all(
        r == [{"address": "http://a:8000", "healthy": True, "status": "active"}] for r in results
    )
    # Within the TTL nothing is fetched again.
    await resolver.resolve("folder-service")
    assert len(calls) == 1


async def test_maintenance_state_fetches_once_under_concurrency():
    calls: list[str] = []
    transport = _counting_transport(calls, {"active": True})

    class _Resolver:
        async def resolve(self, service_type):
            return [{"address": "http://permission:8000"}]

        def pick(self, instances):
            return instances[0]

    state = MaintenanceStateClient(
        client=httpx.AsyncClient(transport=transport), resolver=_Resolver(), cache_ttl_seconds=5.0
    )

    results = await asyncio.gather(*(state.is_active() for _ in range(50)))

    assert calls == ["/maintenance-mode"]
    assert all(results)


def test_parse_limits():
    assert parse_limits("workflow-service=32, document-service = 128,") == {
        "workflow-service": 32,
        "document-service": 128,
    }
    assert parse_limits("") == {}


async def test_bulkhead_refuses_fast_when_a_share_is_full_and_spares_others():
    refused: list[str] = []
    bulkhead = UpstreamBulkhead(
        default_limit=2,
        limits={"document-service": 5},
        wait_seconds=0.1,
        timeout_seconds=5.0,
        on_refused=refused.append,
    )
    assert bulkhead.limit("document-service") == 5
    release = asyncio.Event()

    async def hold(service):
        async with bulkhead.slot(service):
            await release.wait()

    holders = [asyncio.create_task(hold("workflow-service")) for _ in range(2)]
    await asyncio.sleep(0.01)
    assert bulkhead.in_use("workflow-service") == 2

    started = time.perf_counter()
    try:
        async with bulkhead.slot("workflow-service"):
            raise AssertionError("a third slot must not be granted")
    except UpstreamBusyError as exc:
        assert exc.service_type == "workflow-service"
    assert time.perf_counter() - started < 0.5
    assert refused == ["workflow-service"]

    # Another service type is not affected by the full share.
    async with bulkhead.slot("folder-service") as client:
        assert isinstance(client, httpx.AsyncClient)
        assert bulkhead.in_use("folder-service") == 1

    release.set()
    await asyncio.gather(*holders)
    assert bulkhead.in_use("workflow-service") == 0
    # Each service type has its own client (own connection pool).
    assert bulkhead.client("workflow-service") is not bulkhead.client("folder-service")
    await bulkhead.aclose()
