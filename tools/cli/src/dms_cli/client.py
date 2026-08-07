"""HTTP-Client gegen das API-Gateway (3.5) - exakt dasselbe Muster wie
`apps/*-ui/src/lib/api.ts`: `{gateway_url}/api/{service_type}/{path}`,
Bearer-Token, keine direkten Backend-Adressen. Damit gelten fuer das CLI
dieselben Rechte-/Sicherungsstufen wie fuer die Web-UIs (Konzept 6.2)."""

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
    """Ein Aufruf je CLI-Invocation. Versucht bei `401` genau einmal einen
    transparenten Refresh + Retry (siehe Architekturentscheidung in
    PROGRESS.md: die Web-UIs rufen `refreshToken()` bislang nirgends auf -
    fuer ein CLI, dessen Access-Token ueber viele kurze Prozessaufrufe
    hinweg abläuft, ist das aber notwendig)."""

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
        # Ueber self._client (nicht die freie `refresh()`-Funktion), damit der
        # Refresh denselben Transport wie alle anderen Aufrufe verwendet - in
        # Tests den injizierten MockTransport, im Betrieb den echten.
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
