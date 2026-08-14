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
    created = client.post(
        "/ad-group-mappings",
        json={"ad_group_name": "sales", "role_name": "dms-sales-role"},
        headers=domain_admin_auth_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["ad_group_name"] == "sales"
    assert body["role_name"] == "dms-sales-role"
    assert body["id"] is not None
    assert body["created_at"] is not None

    listed = client.get("/ad-group-mappings", headers=domain_admin_auth_headers)
    assert listed.status_code == 200
    assert any(m["id"] == body["id"] for m in listed.json())

    deleted = client.delete(f"/ad-group-mappings/{body['id']}", headers=domain_admin_auth_headers)
    assert deleted.status_code == 204

    listed_after = client.get("/ad-group-mappings", headers=domain_admin_auth_headers)
    assert not any(m["id"] == body["id"] for m in listed_after.json())


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
    ).json()

    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    before = client.get("/me", headers=headers)
    assert mapped_role in before.json()["realm_roles"]

    delete_resp = client.delete(
        f"/ad-group-mappings/{created['id']}", headers=domain_admin_auth_headers
    )
    assert delete_resp.status_code == 204

    after = client.get("/me", headers=headers)
    assert mapped_role not in after.json()["realm_roles"]
