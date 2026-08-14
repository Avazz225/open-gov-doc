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
