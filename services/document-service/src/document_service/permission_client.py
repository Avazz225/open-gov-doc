import httpx


class PermissionServiceClient:
    """HTTP-Client gegen den Permission Service (4.1) - Document Service
    prüft damit erstmals selbst eine echte Leserechtsprüfung (bislang nur
    über den `ApprovalClient` für das Vier-Augen-Prinzip angebunden, siehe
    docs/architecture.md "Autorisierung ... an mehreren Stellen weiterhin
    nicht durchgesetzt"). Bewusst nur für den Freigabelink (4.2a, P14-S10)
    genutzt - ein Dokument, das ANONYM über einen Link erreichbar wird,
    rechtfertigt eine echte Prüfung an der Erzeugungsstelle, unabhängig
    davon, dass reguläre Dokumentzugriffe im übrigen System weiterhin
    ungeprüft bleiben (dieselbe, bereits dokumentierte Lücke)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def check_read(self, *, principal_id: str, resource_id: str) -> bool:
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": "document.read",
                "access_type": "read",
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def check_write(self, *, principal_id: str, resource_id: str) -> bool:
        """Office-Direktbearbeitung (Post-Roadmap-Feature): ein WebDAV-Edit-
        Token gewährt tatsächliche Schreibrechte, nicht nur Lesezugriff wie
        ein Freigabelink - deshalb hier `access_type="write"` statt
        `check_read`s `"read"`."""
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": "document.write",
                "access_type": "write",
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        """Post-Roadmap Phase 19 Session 10 (ADR 0075) - Domain-Admin-
        Capability-Prüfung (`admin.legal_hold`), anders als `check_read`/
        `check_write` oben keine ressourcenskalierte RBAC-Prüfung, sondern
        eine globale Rolle an der Wurzelressource."""
        response = await self._client.get(f"/effective-permissions/{principal_id}/root")
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def close(self) -> None:
        await self._client.aclose()
