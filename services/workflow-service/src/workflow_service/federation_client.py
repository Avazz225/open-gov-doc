import httpx


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


class FederationHubClient:
    """HTTP-Client gegen den Federation Hub (7.4, P6-S9) - gleiches
    Dünn-Wrapper-Muster wie `permission_client.py`/`signature_client.py`. Der
    Hub ist bewusst kein interner Service dieser Installation (keine
    Registry-/Gateway-Anbindung), daher eine feste, separat konfigurierte
    Basis-URL statt Registry-Discovery, siehe `settings.federation_hub_base_url`."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)

    async def get_hub_public_key(self) -> str:
        response = await self._client.get("/public-key")
        response.raise_for_status()
        return response.json()["public_key_pem"]

    async def register(
        self,
        *,
        api_key: str | None,
        installation_id: str,
        display_name: str,
        callback_base_url: str,
        public_key_pem: str,
        version: str,
        min_compatible_peer_version: str,
    ) -> dict:
        response = await self._client.post(
            "/installations",
            json={
                "id": installation_id,
                "display_name": display_name,
                "callback_base_url": callback_base_url,
                "public_key_pem": public_key_pem,
                "version": version,
                "min_compatible_peer_version": min_compatible_peer_version,
            },
            headers=_auth_headers(api_key) if api_key else {},
        )
        response.raise_for_status()
        return response.json()

    async def list_installations(self) -> list[dict]:
        response = await self._client.get("/installations")
        response.raise_for_status()
        return response.json()

    async def create_handover(
        self,
        api_key: str,
        *,
        handover_id: str,
        to_installation_id: str,
        process_type: str,
        encrypted_payload: str,
    ) -> dict:
        response = await self._client.post(
            "/handovers",
            json={
                "handover_id": handover_id,
                "to_installation_id": to_installation_id,
                "process_type": process_type,
                "encrypted_payload": encrypted_payload,
            },
            headers=_auth_headers(api_key),
        )
        response.raise_for_status()
        return response.json()

    async def send_result(
        self, api_key: str, handover_id: str, *, outcome: str, encrypted_result: str
    ) -> dict:
        response = await self._client.post(
            f"/handovers/{handover_id}/result",
            json={"outcome": outcome, "encrypted_result": encrypted_result},
            headers=_auth_headers(api_key),
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
