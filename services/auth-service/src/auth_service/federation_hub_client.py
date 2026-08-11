import json

import httpx

from auth_service import federation_crypto


def _signed_headers(installation_id: str, private_key_pem: bytes, body: bytes) -> dict[str, str]:
    """Identisches Schema wie `workflow_service.federation_client`s
    gleichnamige Funktion (ADR 0039) - der Hub prüft die Signatur gegen den
    bereits registrierten `public_key_pem` dieser (Kontaktsuche-)Installation."""
    return {
        "X-Installation-Id": installation_id,
        "X-Installation-Signature": federation_crypto.sign_body(private_key_pem, body),
    }


class FederationHubClient:
    """Dünner HTTP-Client gegen den Federation Hub (7.4) - nur die für die
    Kontaktsuche benötigte Teilmenge von `workflow_service.
    FederationHubClient` (Registrierung + Adressbuch-Lesen), keine
    Handover-Methoden."""

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
