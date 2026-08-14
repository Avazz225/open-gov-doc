"""HTTP client against the API gateway (3.5) - exactly the same pattern as
`apps/*-ui/src/lib/api.ts`: `{gateway_url}/api/{service_type}/{path}`,
bearer token, no direct backend addresses. This means the same permission/
security levels apply to the CLI as to the web UIs (Concept 6.2)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from dms_cli.credentials import Credentials


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class AuthRequiredError(Exception):
    pass


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    return str(detail if detail is not None else body)


def login(gateway_url: str, username: str, password: str) -> dict:
    response = httpx.post(
        f"{gateway_url.rstrip('/')}/api/auth-service/login",
        json={"username": username, "password": password},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise ApiError(response.status_code, _extract_detail(response))
    return response.json()


class GatewayClient:
    """One call per CLI invocation. On `401`, attempts exactly one
    transparent refresh + retry (see architecture decision in
    PROGRESS.md: the web UIs don't call `refreshToken()` anywhere so
    far - but for a CLI whose access token expires over the course of
    many short process invocations, this is necessary)."""

    def __init__(
        self,
        creds: Credentials,
        *,
        transport: httpx.BaseTransport | None = None,
        on_refresh: Callable[[Credentials], None] | None = None,
    ) -> None:
        self._creds = creds
        self._client = httpx.Client(timeout=30.0, transport=transport)
        self._on_refresh = on_refresh

    def _url(self, service_type: str, path: str) -> str:
        return f"{self._creds.gateway_url.rstrip('/')}/api/{service_type}/{path.lstrip('/')}"

    def _refresh_once(self) -> None:
        # Via self._client (not the free `refresh()` function), so the
        # refresh uses the same transport as all other calls - the injected
        # MockTransport in tests, the real one in production.
        if not self._creds.refresh_token:
            raise AuthRequiredError("Sitzung abgelaufen - bitte 'dms login' erneut ausfuehren.")
        response = self._client.post(
            self._url("auth-service", "refresh"),
            json={"refresh_token": self._creds.refresh_token},
        )
        if response.status_code != 200:
            raise AuthRequiredError("Sitzung abgelaufen - bitte 'dms login' erneut ausfuehren.")
        data = response.json()
        self._creds.access_token = data["access_token"]
        self._creds.refresh_token = data["refresh_token"]
        if self._on_refresh:
            self._on_refresh(self._creds)

    def request(
        self,
        method: str,
        service_type: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        _retry: bool = True,
    ) -> httpx.Response:
        if not self._creds.access_token:
            raise AuthRequiredError("Nicht angemeldet - bitte 'dms login' ausfuehren.")
        headers = {"Authorization": f"Bearer {self._creds.access_token}"}
        clean_params = (
            {k: v for k, v in params.items() if v is not None} if params is not None else None
        )
        response = self._client.request(
            method,
            self._url(service_type, path),
            params=clean_params,
            json=json_body,
            headers=headers,
        )
        if response.status_code == 401 and _retry:
            self._refresh_once()
            return self.request(
                method, service_type, path, params=params, json_body=json_body, _retry=False
            )
        if response.status_code >= 400:
            raise ApiError(response.status_code, _extract_detail(response))
        return response

    def get(self, service_type: str, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self.request("GET", service_type, path, params=params)
        return response.json() if response.content else None

    def post(
        self, service_type: str, path: str, *, json_body: Any = None, params: dict | None = None
    ) -> Any:
        response = self.request("POST", service_type, path, json_body=json_body, params=params)
        return response.json() if response.content else None

    def put(self, service_type: str, path: str, *, json_body: Any = None) -> Any:
        response = self.request("PUT", service_type, path, json_body=json_body)
        return response.json() if response.content else None

    def delete(self, service_type: str, path: str) -> Any:
        response = self.request("DELETE", service_type, path)
        return response.json() if response.content else None

    def get_bytes(self, service_type: str, path: str, *, params: dict | None = None) -> bytes:
        return self.request("GET", service_type, path, params=params).content
