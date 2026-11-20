import json
from urllib.parse import quote

import httpx

from workflow_service import federation_crypto

# Page size for walking the hub's paged installation list (P84-S2).
INSTALLATIONS_PAGE_SIZE = 100


def _signed_headers(installation_id: str, private_key_pem: bytes, body: bytes) -> dict[str, str]:
    """P13-S4 (ADR 0039): replaces the `api_key` previously sent as a bearer
    token, issued by the hub - the hub instead verifies that this signature
    matches this installation's already registered `public_key_pem`
    (`X-Installation-Id` tells it which key to look up for that)."""
    return {
        "X-Installation-Id": installation_id,
        "X-Installation-Signature": federation_crypto.sign_body(private_key_pem, body),
    }


class FederationHubClient:
    """HTTP client against the Federation Hub (7.4, P6-S9) - same thin
    wrapper pattern as `permission_client.py`/`signature_client.py`. The
    hub is deliberately not an internal service of this installation (no
    registry/gateway integration), hence a fixed, separately configured
    base URL instead of registry discovery, see
    `settings.federation_hub_base_url`."""

    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        """`timeout` default kept at the original 15s for callers that
        don't override it (e.g. registration/key rotation, small requests)
        - `main.py` passes `settings.federation_hub_request_timeout_seconds`
        for the actual installation client, since `create_handover`'s
        request body can carry a large base64-encoded XDOMEA package
        (Post-Roadmap Phase 43 Session 1, ADR 0147/ADR 0159)."""
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

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
        """Signs with the **own** `private_key_pem` - even for the very
        first registration (the key pair is generated locally before
        `register()` is even called, see `main.py._ensure_federation_
        identity`); the hub checks self-consistency against the
        `public_key_pem` submitted in the same request (ADR 0039)."""
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
        """Signs with the **old** private key (proof of continuity, ADR 0039
        "key rotation") - only after a successful response may the caller
        switch locally to the new key pair."""
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

    async def get_installation(self, installation_id: str) -> dict | None:
        """One installation by id, ``None`` if the hub doesn't know it
        (P84-S2). Use this, not the list, to resolve a single known
        installation - the list is paged on the hub."""
        response = await self._client.get(f"/installations/{quote(installation_id, safe='')}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def list_installations(self) -> list[dict]:
        """The hub's whole address book. The hub pages its list (default 100,
        ordered by id); until P84-S2 this read only the first page, so
        installations sorting past position 100 were silently missing."""
        installations: list[dict] = []
        offset = 0
        while True:
            response = await self._client.get(
                "/installations", params={"limit": INSTALLATIONS_PAGE_SIZE, "offset": offset}
            )
            response.raise_for_status()
            page = response.json()
            installations.extend(page)
            if len(page) < INSTALLATIONS_PAGE_SIZE:
                return installations
            offset += INSTALLATIONS_PAGE_SIZE

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
