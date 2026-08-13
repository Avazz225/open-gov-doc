import html
import re
from urllib.parse import parse_qs, urlparse

import httpx
from auth_service.settings import Settings

settings = Settings()

_REDIRECT_URI = "http://localhost:3000/login/callback/"


def _extract_login_form_action(page_html: str) -> str:
    """Keycloaks eigenes gehostetes Login-Formular (`login.ftl`) - kein
    JSON-API, daher ein gezielter Regex statt eines vollen HTML-Parsers (im
    Projekt bislang keine Abhängigkeit, für dieses einzelne Feld nicht
    gerechtfertigt)."""
    form_tag_match = re.search(r'<form[^>]+id="kc-form-login"[^>]*>', page_html)
    assert form_tag_match, "Keycloaks Login-Formular wurde nicht gefunden"
    action_match = re.search(r'action="([^"]+)"', form_tag_match.group(0))
    assert action_match, "Login-Formular ohne action-Attribut"
    return html.unescape(action_match.group(1))


def test_oidc_authorize_rejects_unknown_redirect_uri_origin(client):
    response = client.get(
        "/oidc/authorize",
        params={"redirect_uri": "http://evil.example/callback/", "state": "abc"},
    )

    assert response.status_code == 400


def test_oidc_authorize_returns_keycloak_authorization_url(client):
    response = client.get("/oidc/authorize", params={"redirect_uri": _REDIRECT_URI, "state": "abc"})

    assert response.status_code == 200
    authorization_url = response.json()["authorization_url"]
    assert authorization_url.startswith(settings.keycloak_base_url)
    assert "client_id=" + settings.keycloak_client_id in authorization_url


def test_oidc_callback_rejects_unknown_redirect_uri_origin(client):
    response = client.post(
        "/oidc/callback",
        json={"code": "irrelevant", "redirect_uri": "http://evil.example/callback/"},
    )

    assert response.status_code == 400


def test_oidc_full_redirect_round_trip_returns_token_response(client, test_user):
    """Der Fallback-Pfad, den jede Installation ohne echtes Kerberos-Ticket
    zuerst durchläuft (sandbox-bedingt IMMER dieser Pfad, siehe Plan): Folgt
    der `authorization_url` zu Keycloaks eigenem gehosteten Formular, "füllt"
    es mit den echten `test_user`-Zugangsdaten aus, folgt dem Redirect zurück
    zu `redirect_uri?code=...&state=...` und tauscht den Code über `POST
    /oidc/callback` gegen dieselbe `TokenResponse`-Form wie `/login`."""
    authorize_response = client.get(
        "/oidc/authorize", params={"redirect_uri": _REDIRECT_URI, "state": "roundtrip-state"}
    )
    assert authorize_response.status_code == 200
    authorization_url = authorize_response.json()["authorization_url"]

    with httpx.Client(follow_redirects=True) as browser:
        login_page = browser.get(authorization_url)
        assert login_page.status_code == 200
        action_url = _extract_login_form_action(login_page.text)

        submit_response = browser.post(
            action_url,
            data={"username": test_user["username"], "password": test_user["password"]},
            follow_redirects=False,
        )

    assert submit_response.status_code in (302, 303)
    redirect_location = submit_response.headers["location"]
    parsed = urlparse(redirect_location)
    assert parsed.path == "/login/callback/"
    query = parse_qs(parsed.query)
    assert query.get("state") == ["roundtrip-state"]
    code = query["code"][0]

    callback_response = client.post(
        "/oidc/callback", json={"code": code, "redirect_uri": _REDIRECT_URI}
    )

    assert callback_response.status_code == 200
    body = callback_response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"]


def test_sso_config_defaults_to_disabled(client):
    response = client.get("/sso-config")

    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_put_sso_config_requires_authentication(client):
    response = client.put("/sso-config", json={"enabled": True})

    assert response.status_code == 401


def test_put_sso_config_requires_user_management_capability(client, test_user):
    login = client.post("/login", json=test_user).json()

    response = client.put(
        "/sso-config",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )

    assert response.status_code == 403


def test_sso_config_roundtrip(client, domain_admin_auth_headers):
    put_response = client.put(
        "/sso-config", json={"enabled": True}, headers=domain_admin_auth_headers
    )
    assert put_response.status_code == 200
    assert put_response.json()["enabled"] is True

    get_response = client.get("/sso-config")
    assert get_response.status_code == 200
    assert get_response.json()["enabled"] is True


def test_logout_invalidates_refresh_token(client, test_user):
    """Beendet die Sitzung wirklich auf Keycloak-Seite (nicht nur lokal) -
    ein danach erneut versuchter `/refresh` mit demselben Token muss
    fehlschlagen, sonst wäre `/logout` nur ein No-Op."""
    tokens = client.post("/login", json=test_user).json()

    logout_response = client.post("/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout_response.status_code == 204

    refresh_response = client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_response.status_code == 401
