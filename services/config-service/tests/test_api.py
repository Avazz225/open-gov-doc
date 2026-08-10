"""Konfigurationsimport/-export (7.3, P12-S3). Läuft wie `webdav-connector`/
`migration-service` gegen den echten, per docker-compose laufenden Container
(kein In-Prozess-`TestClient`) - `config-service` selbst hat kein eigenes
Postgres-Schema, jede Kategorie wird direkt gegen den echten Owner-Service
(object-type-service/workflow-service/permission-service/monitoring-service)
gelesen/geschrieben, kein Mocking der Nachbar-Services."""

import os
import uuid

import httpx

# Eigenständig gelesen statt aus conftest importiert (siehe dortiger
# Kommentar: `from conftest import x` ist mit `--import-mode=importlib`
# über Testmodule hinweg nicht zuverlässig, gleiches Muster wie
# workflow-service/test_federation.py).
CONFIG_SERVICE_URL = os.environ.get("TEST_CONFIG_SERVICE_URL", "http://localhost:8029")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")


def _client() -> httpx.Client:
    return httpx.Client(base_url=CONFIG_SERVICE_URL, timeout=30.0)


def test_healthz():
    with _client() as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "config-service"


def test_export_returns_all_categories_by_default():
    with _client() as client:
        response = client.get("/config/export")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    for category in ("object_types", "workflows", "roles", "approval_config"):
        assert body[category] is not None
    assert body["sensor_config"] is not None
    assert "global_default" in body["sensor_config"]
    assert body["federation_config"] is not None
    assert "min_compatible_peer_version" in body["federation_config"]


def test_export_with_categories_filter_returns_only_requested():
    with _client() as client:
        response = client.get("/config/export", params={"categories": "roles"})
    assert response.status_code == 200
    body = response.json()
    assert body["roles"] is not None
    assert body["object_types"] is None
    assert body["workflows"] is None
    assert body["approval_config"] is None
    assert body["sensor_config"] is None
    assert body["federation_config"] is None


def test_export_with_unknown_category_returns_422():
    with _client() as client:
        response = client.get("/config/export", params={"categories": "does-not-exist"})
    assert response.status_code == 422


def test_compare_identical_document_against_itself_reports_no_differences():
    with _client() as client:
        exported = client.get("/config/export", params={"categories": "roles"}).json()
        response = client.post(
            "/config/compare",
            json={"compare": exported, "base": exported, "categories": ["roles"]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["categories"]["roles"]["differing"] == {}
    assert body["categories"]["roles"]["only_in_base"] == []
    assert body["categories"]["roles"]["only_in_compare"] == []


def test_compare_without_base_uses_own_live_export():
    """7.5-Anwendungsfall: "was würde sich ändern, wenn ich dieses Dokument
    importiere" - `base` weglassen zieht den eigenen aktuellen Stand heran."""
    role_name = f"config-compare-test-{uuid.uuid4().hex[:8]}"
    with _client() as client:
        response = client.post(
            "/config/compare",
            json={
                "compare": {
                    "schema_version": "1.0",
                    "exported_at": "2026-01-01T00:00:00Z",
                    "roles": [{"name": role_name, "description": "d", "permissions": ["read"]}],
                },
                "categories": ["roles"],
            },
        )
    assert response.status_code == 200
    body = response.json()
    # Eigener Live-Export enthält bereits diverse Rollen aus dem laufenden
    # Dev-Stack - nur prüfen, dass die neue Rolle als "nur in compare" auftaucht
    # und nicht faelschlich unter "nur in base" erscheint.
    assert body["categories"]["roles"]["only_in_compare"] == [role_name]
    assert role_name not in body["categories"]["roles"]["only_in_base"]


def test_compare_detects_differing_role_permissions():
    role_name = f"config-compare-diff-{uuid.uuid4().hex[:8]}"
    base = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "roles": [{"name": role_name, "description": "d", "permissions": ["read"]}],
    }
    compare_doc = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "roles": [{"name": role_name, "description": "d", "permissions": ["read", "write"]}],
    }
    with _client() as client:
        response = client.post(
            "/config/compare",
            json={"base": base, "compare": compare_doc, "categories": ["roles"]},
        )
    assert response.status_code == 200
    differing = response.json()["categories"]["roles"]["differing"]
    assert differing[role_name]["permissions"] == {"base": ["read"], "compare": ["read", "write"]}


def test_compare_matches_names_via_ignore_regex():
    suffix = uuid.uuid4().hex[:8]
    base_name = f"100_delta_test_{suffix}"
    compare_name = f"101__delta_test_{suffix}"
    base = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "roles": [{"name": base_name, "description": "d", "permissions": ["read"]}],
    }
    compare_doc = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "roles": [{"name": compare_name, "description": "d", "permissions": ["read"]}],
    }
    with _client() as client:
        without_regex = client.post(
            "/config/compare",
            json={"base": base, "compare": compare_doc, "categories": ["roles"]},
        )
        with_regex = client.post(
            "/config/compare",
            json={
                "base": base,
                "compare": compare_doc,
                "categories": ["roles"],
                "ignore_regex": {"*": r"^\d+_+"},
            },
        )
    assert without_regex.json()["categories"]["roles"]["only_in_base"] == [base_name]
    assert without_regex.json()["categories"]["roles"]["only_in_compare"] == [compare_name]
    with_regex_roles = with_regex.json()["categories"]["roles"]
    assert with_regex_roles["only_in_base"] == []
    assert with_regex_roles["only_in_compare"] == []
    assert with_regex_roles["identical"] == [base_name]


def test_compare_with_invalid_ignore_regex_returns_422():
    with _client() as client:
        response = client.post(
            "/config/compare",
            json={
                "compare": {"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
                "ignore_regex": {"*": "("},
            },
        )
    assert response.status_code == 422


def test_compare_with_unknown_category_returns_422():
    with _client() as client:
        response = client.post(
            "/config/compare",
            json={
                "compare": {"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
                "categories": ["does-not-exist"],
            },
        )
    assert response.status_code == 422


def test_import_without_principal_header_returns_403():
    with _client() as client:
        response = client.post(
            "/config/import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
        )
    assert response.status_code == 403


def test_import_with_unauthorized_principal_returns_403():
    with _client() as client:
        response = client.post(
            "/config/import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
            headers={"X-DMS-Principal": "irgendein-nutzer-ohne-rolle"},
        )
    assert response.status_code == 403


def test_import_with_fleet_agent_key_bypasses_rbac():
    """3a/P13-S2: derselbe installationsweite Fleet-Agent-Schlüssel wie bei
    license-service (`DMS_FLEET_AGENT_API_KEY`, hier per Compose-Default
    `dev-fleet-agent-key` im gebündelten Dev-/Test-Stack) - kein
    `X-DMS-Principal` nötig."""
    with _client() as client:
        response = client.post(
            "/config/import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
            headers={"Authorization": "Bearer dev-fleet-agent-key"},
        )
    assert response.status_code == 200


def test_import_with_wrong_fleet_agent_key_returns_403():
    with _client() as client:
        response = client.post(
            "/config/import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
            headers={"Authorization": "Bearer not-the-right-key"},
        )
    assert response.status_code == 403


def test_import_with_unsupported_schema_version_returns_422(authorized_principal):
    with _client() as client:
        response = client.post(
            "/config/import",
            json={"schema_version": "99.0", "exported_at": "2026-01-01T00:00:00Z"},
            headers={"X-DMS-Principal": authorized_principal},
        )
    assert response.status_code == 422


def test_import_role_creates_then_updates_by_name(authorized_principal):
    role_name = f"config-import-test-{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "roles": [{"name": role_name, "description": "erste Version", "permissions": ["read"]}],
    }
    with _client() as client:
        first = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert first.status_code == 200
        assert first.json()["results"]["roles"] == {
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        }

        payload["roles"][0]["description"] = "zweite Version"
        payload["roles"][0]["permissions"] = ["read", "write"]
        second = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert second.status_code == 200
        assert second.json()["results"]["roles"] == {
            "created": 0,
            "updated": 1,
            "skipped": 0,
            "errors": [],
        }

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        roles = permission_client.get("/roles").json()
    role = next(r for r in roles if r["name"] == role_name)
    assert role["description"] == "zweite Version"
    assert role["permissions"] == ["read", "write"]


def test_import_approval_config_is_upsert(authorized_principal):
    action_type = f"config.import.test.{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "approval_config": [{"action_type": action_type, "requires_approval": True}],
    }
    with _client() as client:
        response = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
    assert response.status_code == 200
    assert response.json()["results"]["approval_config"] == {
        "created": 0,
        "updated": 1,
        "skipped": 0,
        "errors": [],
    }

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        configs = permission_client.get("/approval-config").json()
    entry = next(c for c in configs if c["action_type"] == action_type)
    assert entry["requires_approval"] is True


def test_export_import_roundtrip_preserves_role_count(authorized_principal):
    with _client() as client:
        exported = client.get("/config/export", params={"categories": "roles"}).json()
        reimport = client.post(
            "/config/import",
            json=exported,
            params={"categories": "roles"},
            headers={"X-DMS-Principal": authorized_principal},
        )
    assert reimport.status_code == 200
    result = reimport.json()["results"]["roles"]
    # Alle bereits vorhandenen Rollen werden per Name gefunden -> ausschliesslich
    # Updates, keine Duplikate (Upsert-Garantie, 7.3).
    assert result["created"] == 0
    assert result["errors"] == []
    assert result["updated"] == len(exported["roles"])


def test_import_federation_config_updates_workflow_service(authorized_principal):
    """7.4/P13-S3: Versionskompatibilitätsspanne ist jetzt eine reguläre
    7.3-Kategorie - Import wirkt tatsächlich auf `workflow-service`."""
    new_version = f"9.{uuid.uuid4().hex[:6]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "federation_config": {"version": new_version, "min_compatible_peer_version": "1.0"},
    }
    with _client() as client:
        response = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
    assert response.status_code == 200
    assert response.json()["results"]["federation_config"] == {
        "created": 0,
        "updated": 1,
        "skipped": 0,
        "errors": [],
    }

    with httpx.Client(base_url=WORKFLOW_SERVICE_URL) as workflow_client:
        config = workflow_client.get("/federation/config").json()
    assert config["version"] == new_version
