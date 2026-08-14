import json

import httpx

from auth_service import federation_crypto


def _signed_headers(installation_id: str, private_key_pem: bytes, body: bytes) -> dict[str, str]:
    """Identical scheme to `workflow_service.federation_client`'s
    function of the same name (ADR 0039) - the Hub checks the signature
    against this (contact-directory) installation's already-registered
    `public_key_pem`."""
    return {
        "X-Installation-Id": installation_id,
        "X-Installation-Signature": federation_crypto.sign_body(private_key_pem, body),
    }


class FederationHubClient:
    """Thin HTTP client against the Federation Hub (7.4) - only the subset
    of `workflow_service.FederationHubClient` needed for the contact
    directory search (registration + address book reading), no handover
    methods."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)

    async def register(
        self,
        *,
        installation_id: str,
        private_key_pem: bytes,
        display_name: str,
        callback_base_url: str,
        public_key_pem: str,
        version: str,
        min_compatible_peer_version: str,
        supported_process_types: list[str],
    ) -> dict:
        body = json.dumps(
            {
                "id": installation_id,
                "display_name": display_name,
                "callback_base_url": callback_base_url,
                "public_key_pem": public_key_pem,
                "version": version,
                "min_compatible_peer_version": min_compatible_peer_version,
                "supported_process_types": supported_process_types,
            }
        ).encode("utf-8")
        response = await self._client.post(
            "/installations",
            content=body,
            headers={
                "Content-Type": "application/json",
                **_signed_headers(installation_id, private_key_pem, body),
            },
        )
        response.raise_for_status()
        return response.json()

    async def list_installations(self) -> list[dict]:
        response = await self._client.get("/installations")
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
