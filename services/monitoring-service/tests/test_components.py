"""Component inventory (P100-S1, ADR 0271)."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from monitoring_service.clients import RegistryInstance
from monitoring_service.main import app

ADMIN = {"x-dms-principal": "alice"}


def _component(name, version, ecosystem="PyPI", own=False):
    purl = f"pkg:{'pypi' if ecosystem == 'PyPI' else ecosystem.lower()}/{name}@{version}"
    props = [{"name": "dms:ecosystem", "value": ecosystem}]
    if own:
        props.append({"name": "dms:own", "value": "true"})
    return {"type": "library", "name": name, "version": version, "purl": purl, "properties": props}


def _report(service, components, fingerprint="f1", matches=True):
    return {
        "service": service,
        "fingerprint": fingerprint,
        "document": {"components": components},
        "image_file": {"matches_image": matches, "created": "2026-11-27T10:00:00+00:00"},
    }


DOC_A = [_component("urllib3", "2.2.1"), _component("openssl", "3.5.5", "Debian")]
DOC_B = [_component("urllib3", "2.3.0"), _component("dms-common", "0.1.0", own=True)]


def _transport(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if (
        "/component-report" in url
        and request.headers.get("x-dms-principal") != "monitoring-service"
    ):
        return httpx.Response(403)
    if url == "http://doc-1:8000/component-report":
        return httpx.Response(200, json=_report("document-service", DOC_A))
    if url == "http://doc-2:8000/component-report":
        # A replica on another image.
        return httpx.Response(200, json=_report("document-service", DOC_A, fingerprint="f2"))
    if url == "http://perm-1:8000/component-report":
        return httpx.Response(200, json=_report("permission-service", DOC_B, matches=False))
    if url == "http://old-1:8000/component-report":
        return httpx.Response(404)
    if url == "http://admin-ui/META-INF/components.cdx.json":
        return httpx.Response(200, json={"components": [_component("react", "18.3.1", "npm")]})
    return httpx.Response(500)


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.registry_client = AsyncMock()
        app.state.registry_client.all_active_instances.return_value = [
            RegistryInstance("doc-1", "document-service", "http://doc-1:8000", []),
            RegistryInstance("doc-2", "document-service", "http://doc-2:8000", []),
            RegistryInstance("perm-1", "permission-service", "http://perm-1:8000", []),
            RegistryInstance("old-1", "legacy-service", "http://old-1:8000", []),
        ]
        original_client = app.state.scrape_client
        app.state.scrape_client = httpx.AsyncClient(transport=httpx.MockTransport(_transport))
        from monitoring_service import main

        original_urls = main.settings.frontend_component_urls
        main.settings.frontend_component_urls = {
            "admin-ui": "http://admin-ui/META-INF/components.cdx.json"
        }
        try:
            yield c
        finally:
            main.settings.frontend_component_urls = original_urls
            app.state.scrape_client = original_client


def test_summary_per_service(client):
    summaries = {
        s["service"]: s for s in client.get("/components?refresh=true", headers=ADMIN).json()
    }
    document = summaries["document-service"]
    assert document["instances"] == 2
    assert document["ecosystems"] == {"Debian": 1, "PyPI": 1}
    assert document["replicas_consistent"] is False
    assert summaries["permission-service"]["matches_image"] is False
    assert summaries["legacy-service"]["error"].startswith("liefert keine Komponenten")
    assert summaries["admin-ui"]["kind"] == "frontend"
    assert summaries["admin-ui"]["ecosystems"] == {"npm": 1}


def test_search_and_service_detail(client):
    client.get("/components?refresh=true", headers=ADMIN)
    rows = client.get("/components/search", params={"q": "urllib3"}, headers=ADMIN).json()
    assert [(r["service"], r["version"]) for r in rows] == [
        ("document-service", "2.2.1"),
        ("permission-service", "2.3.0"),
    ]
    detail = client.get("/components/permission-service", headers=ADMIN).json()
    assert {c["name"]: c["own"] for c in detail["components"]} == {
        "urllib3": False,
        "dms-common": True,
    }
    assert client.get("/components/nope", headers=ADMIN).status_code == 404


def test_export_is_cyclonedx_with_the_services_per_component(client):
    client.get("/components?refresh=true", headers=ADMIN)
    document = client.get("/components/export", headers=ADMIN).json()
    assert document["bomFormat"] == "CycloneDX" and document["specVersion"] == "1.6"
    by_purl = {c["purl"]: c for c in document["components"]}
    services = {
        p["value"]
        for p in by_purl["pkg:debian/openssl@3.5.5"]["properties"]
        if p["name"] == "dms:services"
    }
    assert services == {"document-service"}
    one = client.get("/components/export", params={"service": "admin-ui"}, headers=ADMIN).json()
    assert [c["name"] for c in one["components"]] == ["react"]


def test_needs_the_monitoring_role(client):
    app.state.permission_client.has_permission.return_value = False
    for path in ("/components", "/components/search?q=ab", "/components/export", "/components/x"):
        assert client.get(path, headers=ADMIN).status_code == 403


# --- Vulnerability checks (P100-S2, ADR 0272) -------------------------------------

GHSA = {
    "id": "GHSA-test",
    "aliases": ["CVE-2099-0001"],
    "summary": "urllib3 leaks headers",
    "database_specific": {"severity": "HIGH"},
    "affected": [
        {
            "package": {"ecosystem": "PyPI", "name": "urllib3"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.3.0"}]}],
        }
    ],
}


def test_check_a_record_from_a_file(client):
    client.get("/components?refresh=true", headers=ADMIN)
    result = client.post("/vulnerabilities/check", json={"record": GHSA}, headers=ADMIN).json()
    assert result["source"] == "file"
    assert [(f["service"], f["version"], f["fixed_in"]) for f in result["affected"]] == [
        ("document-service", "2.2.1", "2.3.0")
    ]
    assert [f["service"] for f in result["not_affected"]] == ["permission-service"]
    assert result["records"][0]["severity"] == "HIGH"


def test_check_a_hand_entered_range(client):
    client.get("/components?refresh=true", headers=ADMIN)
    result = client.post(
        "/vulnerabilities/check",
        json={"manual": {"ecosystem": "PyPI", "package": "urllib3", "fixed": "2.2.0"}},
        headers=ADMIN,
    ).json()
    assert result["affected"] == []  # 2.2.1 and 2.3.0 are both past 2.2.0


def test_online_lookup_is_off_by_default_and_input_is_checked(client):
    off = client.post("/vulnerabilities/check", json={"id": "CVE-2099-0001"}, headers=ADMIN)
    assert off.status_code == 409 and "Datei" in off.json()["detail"]
    both = client.post(
        "/vulnerabilities/check", json={"id": "CVE-1", "record": GHSA}, headers=ADMIN
    )
    assert both.status_code == 422
    bad = client.post("/vulnerabilities/check", json={"record": {"x": 1}}, headers=ADMIN)
    assert bad.status_code == 422
    app.state.permission_client.has_permission.return_value = False
    assert (
        client.post("/vulnerabilities/check", json={"record": GHSA}, headers=ADMIN).status_code
        == 403
    )


def test_online_lookup_follows_the_alias(client, monkeypatch):
    from monitoring_service import main

    monkeypatch.setattr(main.settings, "vulnerability_lookup_enabled", True)
    monkeypatch.setattr(main.settings, "vulnerability_lookup_url", "https://osv.test")
    cve = {"id": "CVE-2099-0001", "aliases": ["GHSA-test"], "affected": []}

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            vid = request.url.path.rsplit("/", 1)[1]
            return (
                httpx.Response(200, json={"CVE-2099-0001": cve, "GHSA-test": GHSA}[vid])
                if vid
                in (
                    "CVE-2099-0001",
                    "GHSA-test",
                )
                else httpx.Response(404)
            )

        kwargs.pop("trust_env", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)
    client.get("/components?refresh=true", headers=ADMIN)
    result = client.post(
        "/vulnerabilities/check", json={"id": "CVE-2099-0001"}, headers=ADMIN
    ).json()
    assert result["source"] == "https://osv.test"
    assert {r["id"] for r in result["records"]} == {"CVE-2099-0001", "GHSA-test"}
    assert [f["service"] for f in result["affected"]] == ["document-service"]
    assert client.get("/vulnerabilities/config", headers=ADMIN).json()["online"] is True
