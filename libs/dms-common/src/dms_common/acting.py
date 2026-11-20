"""Who is acting on a request (P85, ADR 0240).

The acting identity is always the request's principal - never a body field
like ``approved_by`` or ``deleted_by``:

- a user call carries ``X-DMS-Principal`` set by the gateway from the
  verified token (the gateway strips every client-sent ``X-DMS-*`` header,
  so a client can forge neither header used here);
- a service call carries the calling service's own name in
  ``X-DMS-Principal``;
- a service acting *for* a user additionally sends ``X-DMS-On-Behalf-Of``.
  That header is honored only when the principal is one of the system's own
  service names (``SERVICE_PRINCIPALS``) - a user can't act on behalf of
  anyone. Audit records both.

Backend ports are loopback-bound (ADR 0230 and its P84-S3 follow-up), so
only the gateway and the services themselves can set these headers.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.exceptions import HTTPException
from starlette.requests import Request

PRINCIPAL_HEADER = "X-DMS-Principal"
ON_BEHALF_OF_HEADER = "X-DMS-On-Behalf-Of"

# Every service of this system, by its compose/service name - the values
# services put into X-DMS-Principal for their own calls. Keep in sync with
# `services/` (a test in each consuming service is not needed: the
# dms-common test checks the list against the repository's services/ tree).
SERVICE_PRINCIPALS: frozenset[str] = frozenset(
    {
        "archival-service",
        "audit-service",
        "auth-service",
        "case-service",
        "cmis-connector",
        "config-service",
        "document-service",
        "favorite-service",
        "federation-hub-service",
        "fleet-management-service",
        "folder-service",
        "gateway-service",
        "license-service",
        "mail-connector",
        "migration-service",
        "monitoring-service",
        "notification-service",
        "object-type-service",
        "ocr-service",
        "permission-service",
        "plugin-orchestration-service",
        "query-service",
        "registry-service",
        "rendering-service",
        "reporting-service",
        "search-service",
        "signature-service",
        "storage-service",
        "teamspace-service",
        "virus-scan-service",
        "webdav-connector",
        "workflow-service",
    }
)


@dataclass(frozen=True)
class ActingIdentity:
    """``principal`` called; ``on_behalf_of`` is the user a service acted
    for (``None`` for a direct call). ``actor`` is who the action is
    attributed to - the user when a service acted on their behalf."""

    principal: str
    on_behalf_of: str | None = None

    @property
    def actor(self) -> str:
        return self.on_behalf_of or self.principal

    @property
    def is_service(self) -> bool:
        return self.principal in SERVICE_PRINCIPALS


def resolve_acting_identity(principal: str, on_behalf_of: str = "") -> ActingIdentity | None:
    """``None`` without a principal. ``on_behalf_of`` is dropped unless the
    principal is a service principal."""
    principal = (principal or "").strip()
    on_behalf_of = (on_behalf_of or "").strip()
    if not principal:
        return None
    if on_behalf_of and principal in SERVICE_PRINCIPALS:
        return ActingIdentity(principal=principal, on_behalf_of=on_behalf_of)
    return ActingIdentity(principal=principal)


def require_acting_identity(request: Request) -> ActingIdentity:
    """FastAPI/Starlette dependency: the acting identity, or ``401`` when the
    request carries no principal (ADR 0240: no principal, no write)."""
    identity = resolve_acting_identity(
        request.headers.get(PRINCIPAL_HEADER, ""), request.headers.get(ON_BEHALF_OF_HEADER, "")
    )
    if identity is None:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    return identity


def acting_headers(service_principal: str, on_behalf_of: str | None = None) -> dict[str, str]:
    """Headers for a service-to-service call: the calling service's own
    principal, plus the user it acts for when there is one."""
    headers = {PRINCIPAL_HEADER: service_principal}
    if on_behalf_of:
        headers[ON_BEHALF_OF_HEADER] = on_behalf_of
    return headers


def on_behalf_headers(service_principal: str, actor: str | None) -> dict[str, str]:
    """Headers for a service call made because of ``actor``: on behalf of
    them when ``actor`` is a person, as the service itself when ``actor`` is
    empty, a service principal, a ``system:*`` job or a service-labelled
    string like ``"migration-service (von X)"`` (P85-S4, ADR 0240)."""
    actor = (actor or "").strip()
    if (
        not actor
        or actor in SERVICE_PRINCIPALS
        or actor.startswith("system:")
        or any(actor.startswith(f"{name} ") for name in SERVICE_PRINCIPALS)
    ):
        return acting_headers(service_principal)
    return acting_headers(service_principal, on_behalf_of=actor)
