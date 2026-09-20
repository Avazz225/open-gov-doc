import uuid

import pytest


@pytest.fixture
def keycloak_group(keycloak_admin):
    """Ein frisches, echtes Keycloak-Gruppen (nicht `permission-service`s
    admin-angelegte `Group`, siehe `models.AdGroupRoleMapping`-Docstring) -
    liefert `(name, group_id)`, räumt danach auf."""
    name = f"ad-group-test-{uuid.uuid4().hex[:8]}"
    group_id = keycloak_admin.create_group(payload={"name": name})
    yield name, group_id
    keycloak_admin.delete_group(group_id)


def _add_user_to_group(keycloak_admin, username: str, group_id: str) -> None:
    user_id = keycloak_admin.get_user_id(username)
    keycloak_admin.group_user_add(user_id, group_id)


def test_list_ad_group_mappings_without_bearer_token_returns_401(client):
    # `Depends(get_current_user)` (HTTPBearer) rejects a completely missing
    # Authorization header itself, before `_require_user_management` (403)
    # is even reached - gleiches Verhalten wie jeder andere per
    # `Depends(get_current_user)` gegatete Endpunkt in diesem Service.
    response = client.get("/ad-group-mappings")
    assert response.status_code == 401


def test_create_ad_group_mapping_without_bearer_token_returns_401(client):
    response = client.post(
        "/ad-group-mappings", json={"ad_group_name": "irrelevant", "role_name": "irrelevant"}
    )
    assert response.status_code == 401


def test_list_ad_group_mappings_with_unprivileged_user_returns_403(client, test_user):
    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.get("/ad-group-mappings", headers=headers)
    assert response.status_code == 403


def test_create_list_delete_ad_group_mapping(client, domain_admin_auth_headers):
    """Antwort-Envelope seit Post-Roadmap Phase 39 Session 3 (ADR 0153) -
    `status`/`mapping`/`approval_request_id`, immer gesetzt unabhaengig von
    aktivierter Vier-Augen-Pflicht (gleiches Muster wie `permission_service.
    schemas.RoleActionResult`)."""
    created = client.post(
        "/ad-group-mappings",
        json={"ad_group_name": "sales", "role_name": "dms-sales-role"},
        headers=domain_admin_auth_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "created"
    assert body["approval_request_id"] is None
    mapping = body["mapping"]
    assert mapping["ad_group_name"] == "sales"
    assert mapping["role_name"] == "dms-sales-role"
    assert mapping["id"] is not None
    assert mapping["created_at"] is not None

    listed = client.get("/ad-group-mappings", headers=domain_admin_auth_headers)
    assert listed.status_code == 200
    assert any(m["id"] == mapping["id"] for m in listed.json())

    deleted = client.delete(
        f"/ad-group-mappings/{mapping['id']}", headers=domain_admin_auth_headers
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "approval_request_id": None}

    listed_after = client.get("/ad-group-mappings", headers=domain_admin_auth_headers)
    assert not any(m["id"] == mapping["id"] for m in listed_after.json())


def test_delete_unknown_ad_group_mapping_returns_404(client, domain_admin_auth_headers):
    response = client.delete("/ad-group-mappings/999999999", headers=domain_admin_auth_headers)
    assert response.status_code == 404


def test_me_merges_role_mapped_from_single_group_membership(
    client, domain_admin_auth_headers, keycloak_admin, keycloak_group, test_user
):
    """Kernszenario (Task-Vorgabe): ein Principal in Gruppe X bekommt die auf
    X gemappte Rolle zusätzlich zu seinen Keycloak-`realm_access.roles`."""
    group_name, group_id = keycloak_group
    _add_user_to_group(keycloak_admin, test_user["username"], group_id)
    mapped_role = f"mapped-role-{uuid.uuid4().hex[:8]}"

    created = client.post(
        "/ad-group-mappings",
        json={"ad_group_name": group_name, "role_name": mapped_role},
        headers=domain_admin_auth_headers,
    )
    assert created.status_code == 201

    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/me", headers=headers)
    assert me.status_code == 200
    assert mapped_role in me.json()["realm_roles"]


def test_me_merges_roles_from_multiple_group_memberships(
    client, domain_admin_auth_headers, keycloak_admin, test_user
):
    """Ein Principal in zwei gemappten Gruppen bekommt beide Rollen."""
    names = []
    group_ids = []
    mapped_roles = []
    try:
        for _ in range(2):
            name = f"ad-group-test-{uuid.uuid4().hex[:8]}"
            group_id = keycloak_admin.create_group(payload={"name": name})
            names.append(name)
            group_ids.append(group_id)
            _add_user_to_group(keycloak_admin, test_user["username"], group_id)
            role = f"mapped-role-{uuid.uuid4().hex[:8]}"
            mapped_roles.append(role)
            resp = client.post(
                "/ad-group-mappings",
                json={"ad_group_name": name, "role_name": role},
                headers=domain_admin_auth_headers,
            )
            assert resp.status_code == 201

        login = client.post(
            "/login",
            json={"username": test_user["username"], "password": test_user["password"]},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        me = client.get("/me", headers=headers)
        assert me.status_code == 200
        realm_roles = me.json()["realm_roles"]
        for role in mapped_roles:
            assert role in realm_roles
    finally:
        for group_id in group_ids:
            keycloak_admin.delete_group(group_id)


def test_me_unaffected_when_group_has_no_mapping(client, keycloak_admin, keycloak_group, test_user):
    """Ein Principal in einer NICHT gemappten Gruppe bleibt unverändert -
    kein Fehler, keine zusätzliche Rolle."""
    _group_name, group_id = keycloak_group
    _add_user_to_group(keycloak_admin, test_user["username"], group_id)

    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/me", headers=headers)
    assert me.status_code == 200
    # Keine ungemappten Überraschungsrollen - realm_roles bleibt bei genau
    # dem, was Keycloak selbst (ohne Gruppenmapping-Beitrag) zugewiesen hat.
    assert not any(role.startswith("mapped-role-") for role in me.json()["realm_roles"])


def test_deleting_mapping_takes_effect_on_next_resolution(
    client, domain_admin_auth_headers, keycloak_admin, keycloak_group, test_user
):
    """Löschen einer Zuordnung wirkt sich ab dem nächsten `/me`-Aufruf aus -
    kein Caching, das die alte Rolle weiter ausliefern würde."""
    group_name, group_id = keycloak_group
    _add_user_to_group(keycloak_admin, test_user["username"], group_id)
    mapped_role = f"mapped-role-{uuid.uuid4().hex[:8]}"

    created = client.post(
        "/ad-group-mappings",
        json={"ad_group_name": group_name, "role_name": mapped_role},
        headers=domain_admin_auth_headers,
    ).json()["mapping"]

    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    before = client.get("/me", headers=headers)
    assert mapped_role in before.json()["realm_roles"]

    delete_resp = client.delete(
        f"/ad-group-mappings/{created['id']}", headers=domain_admin_auth_headers
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "deleted"

    after = client.get("/me", headers=headers)
    assert mapped_role not in after.json()["realm_roles"]


# --- Composite (AND) rules, configurable default role, four-eyes
# (Post-Roadmap Phase 39 Session 3, ADR 0153) -----------------------------


def test_create_composite_rule_requires_at_least_two_groups(client, domain_admin_auth_headers):
    response = client.post(
        "/ad-group-composite-rules",
        json={"role_name": "dms-dual-role", "ad_group_names": ["sales"]},
        headers=domain_admin_auth_headers,
    )
    assert response.status_code == 422


def test_create_list_delete_composite_rule(client, domain_admin_auth_headers):
    created = client.post(
        "/ad-group-composite-rules",
        json={"role_name": "dms-dual-role", "ad_group_names": ["sales", "finance"]},
        headers=domain_admin_auth_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "created"
    rule = body["rule"]
    assert rule["role_name"] == "dms-dual-role"
    assert sorted(rule["ad_group_names"]) == ["finance", "sales"]

    listed = client.get("/ad-group-composite-rules", headers=domain_admin_auth_headers)
    assert listed.status_code == 200
    assert any(r["id"] == rule["id"] for r in listed.json())

    deleted = client.delete(
        f"/ad-group-composite-rules/{rule['id']}", headers=domain_admin_auth_headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    listed_after = client.get("/ad-group-composite-rules", headers=domain_admin_auth_headers)
    assert not any(r["id"] == rule["id"] for r in listed_after.json())


def test_delete_unknown_composite_rule_returns_404(client, domain_admin_auth_headers):
    response = client.delete(
        "/ad-group-composite-rules/999999999", headers=domain_admin_auth_headers
    )
    assert response.status_code == 404


def test_me_requires_all_groups_of_a_composite_rule(
    client, domain_admin_auth_headers, keycloak_admin, test_user
):
    """Kernszenario der AND-Logik: Mitgliedschaft in nur EINER der beiden
    verknuepften Gruppen reicht nicht - erst beide zusammen geben die
    Rolle."""
    names, group_ids = [], []
    try:
        for _ in range(2):
            name = f"ad-group-test-{uuid.uuid4().hex[:8]}"
            group_id = keycloak_admin.create_group(payload={"name": name})
            names.append(name)
            group_ids.append(group_id)

        composite_role = f"dual-role-{uuid.uuid4().hex[:8]}"
        created = client.post(
            "/ad-group-composite-rules",
            json={"role_name": composite_role, "ad_group_names": names},
            headers=domain_admin_auth_headers,
        )
        assert created.status_code == 201

        def _login_and_get_me() -> dict:
            # Keycloak bakes the `groups` claim into the access token AT
            # LOGIN TIME - a group change after that point is invisible to
            # an already-issued token, so this re-logs in fresh after
            # every membership change instead of reusing one token.
            login = client.post(
                "/login",
                json={"username": test_user["username"], "password": test_user["password"]},
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            return client.get("/me", headers=headers).json()

        # Nur die erste Gruppe: die Verbund-Regel greift noch nicht.
        _add_user_to_group(keycloak_admin, test_user["username"], group_ids[0])
        only_one = _login_and_get_me()
        assert composite_role not in only_one["realm_roles"]

        # Beide Gruppen: jetzt greift sie.
        _add_user_to_group(keycloak_admin, test_user["username"], group_ids[1])
        both = _login_and_get_me()
        assert composite_role in both["realm_roles"]
    finally:
        for group_id in group_ids:
            keycloak_admin.delete_group(group_id)


def test_default_role_get_set_reset(client, domain_admin_auth_headers):
    """`PUT` wraps its response since Phase 53 Session 1 (ADR 0171,
    optional four-eyes) - `{status, config, approval_request_id}`, same
    envelope shape as the other four AD-group-mapping mutations. `GET`
    stays a plain, unwrapped resource, unchanged."""
    initial = client.get("/ad-group-mappings/default-role", headers=domain_admin_auth_headers)
    assert initial.status_code == 200
    assert initial.json()["default_role_name"] is None

    updated = client.put(
        "/ad-group-mappings/default-role",
        json={"default_role_name": "dms-basic-role"},
        headers=domain_admin_auth_headers,
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["status"] == "set"
    assert body["config"]["default_role_name"] == "dms-basic-role"

    after_get = client.get("/ad-group-mappings/default-role", headers=domain_admin_auth_headers)
    assert after_get.json()["default_role_name"] == "dms-basic-role"

    reset = client.put(
        "/ad-group-mappings/default-role",
        json={"default_role_name": None},
        headers=domain_admin_auth_headers,
    )
    assert reset.status_code == 200
    assert reset.json()["config"]["default_role_name"] is None


def test_default_role_set_with_approval_required_defers_execution(
    client, domain_admin_auth_headers, approval_config_override
):
    """Regression test (Phase 53 Session 1, ADR 0171): the default-role
    setting previously had no four-eyes gate at all - ADR 0153's own
    deliberate scope cut, reversed here on explicit request. Same pattern
    as `test_create_ad_group_mapping_with_approval_required_defers_creation`."""
    with approval_config_override("auth.ad_group_mapping.default_role_set", requires_approval=True):
        response = client.put(
            "/ad-group-mappings/default-role",
            json={"default_role_name": "dms-basic-role"},
            headers=domain_admin_auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["approval_request_id"] is not None
        assert body["config"] is None

        # Not applied - the default role remains unset.
        after = client.get("/ad-group-mappings/default-role", headers=domain_admin_auth_headers)
        assert after.json()["default_role_name"] is None


def test_me_gets_default_role_when_groups_are_unmapped(
    client, domain_admin_auth_headers, keycloak_admin, keycloak_group, test_user
):
    """Der eigentliche Zielfall der Konfiguration: ein Principal IN einer
    Gruppe, aber ohne passendes Mapping, bekommt die konfigurierte
    Standardrolle statt gar keiner."""
    _group_name, group_id = keycloak_group
    _add_user_to_group(keycloak_admin, test_user["username"], group_id)
    default_role = f"default-role-{uuid.uuid4().hex[:8]}"
    client.put(
        "/ad-group-mappings/default-role",
        json={"default_role_name": default_role},
        headers=domain_admin_auth_headers,
    )

    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/me", headers=headers)
    assert default_role in me.json()["realm_roles"]


def test_me_does_not_get_default_role_without_any_group(
    client, domain_admin_auth_headers, test_user
):
    """Ein Principal OHNE jede AD-Gruppen-Mitgliedschaft bekommt die
    Standardrolle NICHT - "Standard fuer ungemappte Gruppen" setzt
    mindestens eine (nicht passende) Gruppe voraus, siehe
    `ad_group_mapping.resolve_roles_for_groups`s Docstring."""
    default_role = f"default-role-{uuid.uuid4().hex[:8]}"
    client.put(
        "/ad-group-mappings/default-role",
        json={"default_role_name": default_role},
        headers=domain_admin_auth_headers,
    )

    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/me", headers=headers)
    assert default_role not in me.json()["realm_roles"]


def test_me_prefers_matched_role_over_default(
    client, domain_admin_auth_headers, keycloak_admin, keycloak_group, test_user
):
    """Der Standard greift nur, wenn NICHTS gematcht hat - eine tatsaechlich
    gemappte Rolle bleibt unbeeinflusst vom konfigurierten Standard."""
    group_name, group_id = keycloak_group
    _add_user_to_group(keycloak_admin, test_user["username"], group_id)
    mapped_role = f"mapped-role-{uuid.uuid4().hex[:8]}"
    default_role = f"default-role-{uuid.uuid4().hex[:8]}"
    client.post(
        "/ad-group-mappings",
        json={"ad_group_name": group_name, "role_name": mapped_role},
        headers=domain_admin_auth_headers,
    )
    client.put(
        "/ad-group-mappings/default-role",
        json={"default_role_name": default_role},
        headers=domain_admin_auth_headers,
    )

    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/me", headers=headers)
    realm_roles = me.json()["realm_roles"]
    assert mapped_role in realm_roles
    assert default_role not in realm_roles


def test_create_ad_group_mapping_with_approval_required_defers_creation(
    client, domain_admin_auth_headers, approval_config_override
):
    """Vier-Augen-Retrofit (Post-Roadmap Phase 39 Session 3, ADR 0153) -
    gleiches Muster wie permission-service's `test_create_role_with_
    approval_required_defers_creation` (ADR 0130), nur cross-service."""
    with approval_config_override("auth.ad_group_role_mapping.create", requires_approval=True):
        response = client.post(
            "/ad-group-mappings",
            json={"ad_group_name": "gated-group", "role_name": "gated-role"},
            headers=domain_admin_auth_headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["approval_request_id"] is not None
        assert body["mapping"] is None

        listed = client.get("/ad-group-mappings", headers=domain_admin_auth_headers)
        assert not any(m["ad_group_name"] == "gated-group" for m in listed.json())


def test_create_composite_rule_with_approval_required_defers_creation(
    client, domain_admin_auth_headers, approval_config_override
):
    with approval_config_override(
        "auth.ad_group_role_composite_rule.create", requires_approval=True
    ):
        response = client.post(
            "/ad-group-composite-rules",
            json={"role_name": "gated-dual-role", "ad_group_names": ["a", "b"]},
            headers=domain_admin_auth_headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["approval_request_id"] is not None
        assert body["rule"] is None

        listed = client.get("/ad-group-composite-rules", headers=domain_admin_auth_headers)
        assert not any(r["role_name"] == "gated-dual-role" for r in listed.json())
