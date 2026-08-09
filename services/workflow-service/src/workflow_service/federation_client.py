import json

import httpx

from workflow_service import federation_crypto


def _signed_headers(installation_id: str, private_key_pem: bytes, body: bytes) -> dict[str, str]:
    """P13-S4 (ADR 0039): ersetzt den zuvor als Bearer-Token mitgeschickten,
    vom Hub ausgegebenen `api_key` - der Hub verifiziert stattdessen, dass
    diese Signatur zum bereits registrierten `public_key_pem` dieser
    Installation passt (`X-Installation-Id` sagt ihm, welchen Schlüssel er
    dafür nachschlagen soll)."""
    return {
        "X-Installation-Id": installation_id,
        "X-Installation-Signature": federation_crypto.sign_body(private_key_pem, body),
    }


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
        installation_id: str,
        private_key_pem: bytes,
        display_name: str,
        callback_base_url: str,
        public_key_pem: str,
        version: str,
        min_compatible_peer_version: str,
    ) -> dict:
        """Signiert mit dem **eigenen** `private_key_pem` - auch bei der
        allerersten Registrierung (das Schlüsselpaar wird lokal generiert,
        bevor `register()` überhaupt aufgerufen wird, siehe `main.py.
        _ensure_federation_identity`), der Hub prüft dabei Selbstkonsistenz
        gegen das im selben Request eingereichte `public_key_pem` (ADR 0039)."""
        body = json.dumps(
            {
                "id": installation_id,
                "display_name": display_name,
                "callback_base_url": callback_base_url,
                "public_key_pem": public_key_pem,
                "version": version,
                "min_compatible_peer_version": min_compatible_peer_version,
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

    async def rotate_key(
        self, *, installation_id: str, old_private_key_pem: bytes, new_public_key_pem: str
    ) -> dict:
        """Signiert mit dem **alten** privaten Schlüssel (Kontinuitätsnachweis,
        ADR 0039 "Schlüsselrotation") - erst nach erfolgreicher Antwort darf
        der Aufrufer lokal auf das neue Schlüsselpaar umstellen."""
        body = json.dumps({"new_public_key_pem": new_public_key_pem}).encode("utf-8")
        response = await self._client.post(
            f"/installations/{installation_id}/rotate-key",
            content=body,
            headers={
                "Content-Type": "application/json",
                **_signed_headers(installation_id, old_private_key_pem, body),
            },
        )
        response.raise_for_status()
        return response.json()

    async def list_installations(self) -> list[dict]:
        response = await self._client.get("/installations")
        response.raise_for_status()
        return response.json()

    async def create_handover(
        self,
        *,
        installation_id: str,
        private_key_pem: bytes,
        handover_id: str,
        to_installation_id: str,
        process_type: str,
        encrypted_payload: str,
    ) -> dict:
        body = json.dumps(
            {
                "handover_id": handover_id,
                "to_installation_id": to_installation_id,
                "process_type": process_type,
                "encrypted_payload": encrypted_payload,
            }
        ).encode("utf-8")
        response = await self._client.post(
            "/handovers",
            content=body,
            headers={
                "Content-Type": "application/json",
                **_signed_headers(installation_id, private_key_pem, body),
            },
        )
        response.raise_for_status()
        return response.json()

    async def send_result(
        self,
        *,
        installation_id: str,
        private_key_pem: bytes,
        handover_id: str,
        outcome: str,
        encrypted_result: str,
    ) -> dict:
        body = json.dumps({"outcome": outcome, "encrypted_result": encrypted_result}).encode(
            "utf-8"
        )
        response = await self._client.post(
            f"/handovers/{handover_id}/result",
            content=body,
            headers={
                "Content-Type": "application/json",
                **_signed_headers(installation_id, private_key_pem, body),
            },
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
