import httpx
import pytest
from dms_cli import client as client_module
from dms_cli.client import ApiError, AuthRequiredError, GatewayClient
from dms_cli.credentials import Credentials


def test_login_posts_credentials_to_gateway_auth_service(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(
            200,
            json={
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 300,
                "token_type": "bearer",
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(client_module.httpx, "post", fake_post)

    result = client_module.login("http://gw.test", "alice", "secret")

    assert captured["url"] == "http://gw.test/api/auth-service/login"
    assert captured["json"] == {"username": "alice", "password": "secret"}
    assert result["access_token"] == "a"


def test_login_with_wrong_credentials_raises_api_error(monkeypatch):
    def fake_post(url, json, timeout):  # noqa: A002
        return httpx.Response(
            401, json={"detail": "invalid credentials"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(client_module.httpx, "post", fake_post)

    with pytest.raises(ApiError) as exc_info:
        client_module.login("http://gw.test", "alice", "wrong")

    assert exc_info.value.status_code == 401
    assert "invalid credentials" in exc_info.value.detail


def _creds(**overrides) -> Credentials:
    base = {
        "gateway_url": "http://gw.test",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "username": "alice",
    }
    base.update(overrides)
    return Credentials(**base)


def test_get_returns_parsed_json_with_bearer_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"hello": "world"})

    gw = GatewayClient(_creds(), transport=httpx.MockTransport(handler))

    result = gw.get("query-service", "query/events", params={"limit": 5, "actor": None})

    assert result == {"hello": "world"}
    assert captured["url"] == "http://gw.test/api/query-service/query/events?limit=5"
    assert captured["auth"] == "Bearer access-token"


def test_401_triggers_transparent_refresh_and_retry():
    calls: list[str] = []
    refreshed_creds: list[Credentials] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth-service/refresh":
            calls.append("refresh")
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 300,
                    "token_type": "bearer",
                },
            )
        calls.append(f"call:{request.headers.get('authorization')}")
        if request.headers.get("authorization") == "Bearer access-token":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"ok": True})

    creds = _creds()
    gw = GatewayClient(
        creds, transport=httpx.MockTransport(handler), on_refresh=refreshed_creds.append
    )

    result = gw.get("auth-service", "me")

    assert result == {"ok": True}
    assert calls == ["call:Bearer access-token", "refresh", "call:Bearer new-access"]
    assert creds.access_token == "new-access"
    assert refreshed_creds == [creds]


def test_401_without_refresh_token_raises_auth_required():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "expired"})

    gw = GatewayClient(_creds(refresh_token=None), transport=httpx.MockTransport(handler))

    with pytest.raises(AuthRequiredError):
        gw.get("auth-service", "me")


def test_failed_refresh_raises_auth_required():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth-service/refresh":
            return httpx.Response(401, json={"detail": "refresh token expired"})
        return httpx.Response(401, json={"detail": "expired"})

    gw = GatewayClient(_creds(), transport=httpx.MockTransport(handler))

    with pytest.raises(AuthRequiredError):
        gw.get("auth-service", "me")


def test_non_401_error_status_raises_api_error_with_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    gw = GatewayClient(_creds(), transport=httpx.MockTransport(handler))

    with pytest.raises(ApiError) as exc_info:
        gw.get("object-type-service", "object-types/999")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "not found"


def test_request_without_access_token_raises_auth_required():
    gw = GatewayClient(
        _creds(access_token=None), transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )

    with pytest.raises(AuthRequiredError):
        gw.get("auth-service", "me")


def test_post_put_delete_and_get_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"method": "post"})
        if request.method == "PUT":
            return httpx.Response(200, json={"method": "put"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET" and request.url.path.endswith("/package"):
            return httpx.Response(200, content=b"zip-bytes")
        return httpx.Response(200, json={})

    gw = GatewayClient(_creds(), transport=httpx.MockTransport(handler))

    assert gw.post("object-type-service", "object-types", json_body={"name": "x"}) == {
        "method": "post"
    }
    assert gw.put("object-type-service", "object-types/1", json_body={"name": "y"}) == {
        "method": "put"
    }
    assert gw.delete("object-type-service", "object-types/1") is None
    assert gw.get_bytes("archival-service", "case-archival-transfers/1/package") == b"zip-bytes"
