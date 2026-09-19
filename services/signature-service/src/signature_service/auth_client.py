import httpx

from signature_service.connectors.interface import SignerInfo

_SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "signature-service"}


class AuthServiceClient:
    """HTTP client against the Auth Service - `signer_principal_id` remains
    a self-reported field (consistent with `triggered_by`/`approved_by`/
    `completed_by`/`lifted_by` throughout the project), but is - same
    retrofit pattern as with notification-service (P6-S6) - checked against
    a real `auth-service` account and returns the display name/email for
    the AES certificate (3.10: "uniquely attributable to a person").

    **Phase 50 Session 2**: previously logged in as the `users-admin`
    technical account (full domain-admin - user CRUD + AD-group->role
    mapping control) just for this read-only lookup, a real excess-privilege
    exposure. Now asserts the same fixed `X-DMS-Principal: signature-service`
    identity this service already uses against `document-service`
    (`document_client.py`), against the new, narrow
    `GET /users/service-directory` (capability `service.user_lookup`).
    No token/header caching needed any more either, since there's no login
    round-trip."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def resolve_signer(self, principal_id: str) -> SignerInfo | None:
        response = await self._client.get(
            "/users/service-directory", headers=_SYSTEM_PRINCIPAL_HEADERS
        )
        response.raise_for_status()
        for user in response.json():
            if user["username"] == principal_id:
                first = user.get("first_name") or ""
                last = user.get("last_name") or ""
                display_name = f"{first} {last}".strip() or principal_id
                return SignerInfo(
                    principal_id=principal_id,
                    display_name=display_name,
                    email=user.get("email") or "",
                )
        return None

    async def close(self) -> None:
        await self._client.aclose()
