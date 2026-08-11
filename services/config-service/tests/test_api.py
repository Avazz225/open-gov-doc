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
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")
AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")


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
    for category in (
        "object_types",
        "workflows",
        "dmn_definitions",
        "business_calendars",
        "roles",
        "approval_config",
        "realm_roles",
    ):
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
    assert body["dmn_definitions"] is None
    assert body["business_calendars"] is None
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


def test_import_with_fleet_agent_key_no_longer_bypasses_rbac_on_config_import():
    """P17-S1: `POST /config/import` verlangt seit der Trennung von
    `POST /config/fleet-import` ausschließlich RBAC - ein Fleet-Agent-
    Schlüssel ohne `X-DMS-Principal` wird hier jetzt abgelehnt (vorher, als
    beide Zugriffswege denselben Pfad teilten, war das ein Bypass)."""
    with _client() as client:
        response = client.post(
            "/config/import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
            headers={"Authorization": "Bearer dev-fleet-agent-key"},
        )
    assert response.status_code == 403


def test_fleet_import_with_fleet_agent_key_bypasses_rbac():
    """3a/P13-S2, seit P17-S1 unter eigenem Pfad `POST /config/fleet-import`:
    derselbe installationsweite Fleet-Agent-Schlüssel wie bei license-service
    (`DMS_FLEET_AGENT_API_KEY`, hier per Compose-Default `dev-fleet-agent-key`
    im gebündelten Dev-/Test-Stack) - kein `X-DMS-Principal` nötig."""
    with _client() as client:
        response = client.post(
            "/config/fleet-import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
            headers={"Authorization": "Bearer dev-fleet-agent-key"},
        )
    assert response.status_code == 200


def test_fleet_import_with_wrong_fleet_agent_key_returns_403():
    with _client() as client:
        response = client.post(
            "/config/fleet-import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
            headers={"Authorization": "Bearer not-the-right-key"},
        )
    assert response.status_code == 403


def test_fleet_import_without_any_header_returns_403():
    with _client() as client:
        response = client.post(
            "/config/fleet-import",
            json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"},
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


def test_import_returns_applied_status_by_default(authorized_principal):
    """Seit P17-S3 (4.3/14.2) liefert `POST /config/import` das gegatete
    `ImportActionResult` - ohne aktivierte Genehmigungspflicht (Default)
    bleibt das Verhalten unverändert: sofortige Anwendung, `status="applied"`,
    `result` sofort gesetzt."""
    role_name = f"config-import-applied-{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "roles": [{"name": role_name, "description": "d", "permissions": ["read"]}],
    }
    with _client() as client:
        response = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["approval_request_id"] is None
    assert body["result"]["results"]["roles"]["created"] == 1


def test_import_with_approval_required_defers_execution(authorized_principal):
    """Vier-Augen-Vorbelegung des eGov-Pakets (14.2 "Konfigurationsimport") -
    mit aktivierter Genehmigungspflicht wird NICHT sofort importiert, echte
    Integration gegen den lokal laufenden permission-service, gleiches "kein
    Mocking von Sibling-Services"-Muster wie document-service's
    `test_force_release_with_approval_required_defers_execution`."""
    role_name = f"config-import-pending-{uuid.uuid4().hex[:8]}"
    httpx.put(
        f"{PERMISSION_SERVICE_URL}/approval-config/config.import",
        json={"requires_approval": True},
    )
    try:
        payload = {
            "schema_version": "1.0",
            "exported_at": "2026-01-01T00:00:00Z",
            "roles": [{"name": role_name, "description": "d", "permissions": ["read"]}],
        }
        with _client() as client:
            response = client.post(
                "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["result"] is None
        assert body["approval_request_id"] is not None

        # Keine sofortige Ausführung - die Rolle ist (noch) nicht angelegt.
        # Die tatsächliche Anwendung folgt asynchron über consumer.py, sobald
        # das Approval-Event eintrifft (siehe test_consumer.py).
        with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
            roles = permission_client.get("/roles").json()
        assert role_name not in [r["name"] for r in roles]

        with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
            permission_client.post(
                f"/approval-requests/{body['approval_request_id']}/reject",
                json={"rejected_by": "supervisor"},
            )
    finally:
        httpx.put(
            f"{PERMISSION_SERVICE_URL}/approval-config/config.import",
            json={"requires_approval": False},
        )


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
        assert first.json()["result"]["results"]["roles"] == {
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
        assert second.json()["result"]["results"]["roles"] == {
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


def test_import_object_type_roundtrips_classification_level(authorized_principal):
    """Verschlusssachen-Einstufung (2.5, P15-S1, mehrstufig seit P17-S2/14.2)
    - `classification_level` muss den Export/Import-Roundtrip überleben,
    sonst geht die Einstufung einer Dokumentklasse beim Verteilen einer
    Konfiguration stillschweigend verloren (echter, bei P15-S1 selbst
    gefundener Bug in `ObjectTypeExport`/`_OBJECT_TYPE_MUTABLE_FIELDS` für das
    damals noch binäre `is_classified` - dieselbe Klasse Fehler wäre beim
    P17-S2-Umbenennen ebenso leicht wieder möglich gewesen)."""
    type_name = f"config-import-classified-{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "object_types": [
            {
                "name": type_name,
                "applies_to": "document",
                "classification_level": "VS-VERTRAULICH",
            }
        ],
    }
    with _client() as client:
        first = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert first.status_code == 200
        assert first.json()["result"]["results"]["object_types"]["created"] == 1

    with httpx.Client(base_url=OBJECT_TYPE_SERVICE_URL) as object_type_client:
        created = next(
            t for t in object_type_client.get("/object-types").json() if t["name"] == type_name
        )
        assert created["classification_level"] == "VS-VERTRAULICH"

    with _client() as client:
        payload["object_types"][0]["classification_level"] = None
        second = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert second.status_code == 200
        assert second.json()["result"]["results"]["object_types"]["updated"] == 1

    with httpx.Client(base_url=OBJECT_TYPE_SERVICE_URL) as object_type_client:
        updated = next(
            t for t in object_type_client.get("/object-types").json() if t["name"] == type_name
        )
        assert updated["classification_level"] is None

    with _client() as client:
        export = client.get("/config/export", params={"categories": "object_types"})
    exported = next(t for t in export.json()["object_types"] if t["name"] == type_name)
    assert exported["classification_level"] is None


def _dmn_xml(decision_id: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"
             id="Definitions_1" name="definitions"
             namespace="http://camunda.org/schema/1.0/dmn">
  <decision id="{decision_id}" name="Testentscheidung">
    <decisionTable id="DecisionTable_1" hitPolicy="UNIQUE">
      <input id="Input_1" label="Betrag">
        <inputExpression id="InputExpression_1" typeRef="number">
          <text>amount</text>
        </inputExpression>
      </input>
      <output id="Output_1" label="Stufe" name="level" typeRef="string" />
      <rule id="Rule_1">
        <inputEntry id="InputEntry_1"><text>&lt; 1000</text></inputEntry>
        <outputEntry id="OutputEntry_1"><text>"sachbearbeiter"</text></outputEntry>
      </rule>
    </decisionTable>
  </decision>
</definitions>
"""


def test_import_dmn_definition_creates_new_version_at_workflow_service(authorized_principal):
    """7.1/7.3 (P14-S4): `dmn_definitions` ist eine reguläre Kategorie wie
    `workflows` - workflow-service versioniert beim Hochladen unter demselben
    Namen automatisch neu (siehe `apply_dmn_definitions`), jeder Import zählt
    also als "created"."""
    name = f"config-import-dmn-test-{uuid.uuid4().hex[:8]}"
    decision_id = f"decision-{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "dmn_definitions": [{"name": name, "dmn_xml": _dmn_xml(decision_id)}],
    }
    with _client() as client:
        first = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert first.status_code == 200
        assert first.json()["result"]["results"]["dmn_definitions"] == {
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        }

        second = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert second.status_code == 200
        assert second.json()["result"]["results"]["dmn_definitions"]["created"] == 1

    with httpx.Client(base_url=WORKFLOW_SERVICE_URL) as workflow_client:
        definitions = workflow_client.get("/dmn-definitions", params={"name": name}).json()
    assert [d["version"] for d in definitions] == [2, 1]
    assert definitions[0]["decision_id"] == decision_id


def test_import_business_calendar_creates_then_updates_by_name(authorized_principal):
    """7.1/7.3 (P14-S5): `business_calendars` ist - anders als `workflows`/
    `dmn_definitions` - eine Upsert-Kategorie wie `roles` (siehe
    `apply_business_calendars`), kein Versionierungsmuster."""
    name = f"config-import-cal-test-{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "business_calendars": [
            {"name": name, "non_working_dates": ["2026-12-25"], "is_default": False}
        ],
    }
    with _client() as client:
        first = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert first.status_code == 200
        assert first.json()["result"]["results"]["business_calendars"] == {
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        }

        payload["business_calendars"][0]["non_working_dates"] = ["2026-12-25", "2026-12-26"]
        second = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
        assert second.status_code == 200
        assert second.json()["result"]["results"]["business_calendars"] == {
            "created": 0,
            "updated": 1,
            "skipped": 0,
            "errors": [],
        }

    with httpx.Client(base_url=WORKFLOW_SERVICE_URL) as workflow_client:
        calendars = workflow_client.get("/business-calendars").json()
    calendar = next(c for c in calendars if c["name"] == name)
    assert calendar["non_working_dates"] == ["2026-12-25", "2026-12-26"]


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
    assert response.json()["result"]["results"]["approval_config"] == {
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
    result = reimport.json()["result"]["results"]["roles"]
    # Alle bereits vorhandenen Rollen werden per Name gefunden -> ausschliesslich
    # Updates, keine Duplikate (Upsert-Garantie, 7.3).
    assert result["created"] == 0
    assert result["errors"] == []
    assert result["updated"] == len(exported["roles"])


def test_import_realm_roles_creates_new_keycloak_realm_role(authorized_principal):
    """14.1/P17-S1: `realm_roles` ist eine Namensliste, keine `RoleExport`-
    artige Kategorie - Anwendung erfolgt über `auth-service`s
    `POST /realm-roles` (idempotent, `create_realm_role(..., skip_exists=True)`)."""
    role_name = f"config-import-realm-role-{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "realm_roles": [role_name],
    }
    with _client() as client:
        response = client.post(
            "/config/import", json=payload, headers={"X-DMS-Principal": authorized_principal}
        )
    assert response.status_code == 200
    assert response.json()["result"]["results"]["realm_roles"] == {
        "created": 0,
        "updated": 1,
        "skipped": 0,
        "errors": [],
    }

    with httpx.Client(base_url=AUTH_SERVICE_URL) as auth_client:
        roles = auth_client.get("/realm-roles").json()
    assert role_name in [r["name"] for r in roles]

    with _client() as client:
        export = client.get("/config/export", params={"categories": "realm_roles"})
    assert role_name in export.json()["realm_roles"]


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
    assert response.json()["result"]["results"]["federation_config"] == {
        "created": 0,
        "updated": 1,
        "skipped": 0,
        "errors": [],
    }

    with httpx.Client(base_url=WORKFLOW_SERVICE_URL) as workflow_client:
        config = workflow_client.get("/federation/config").json()
    assert config["version"] == new_version
