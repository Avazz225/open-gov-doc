from pathlib import Path

import pytest
from dms_common.acting import (
    ON_BEHALF_OF_HEADER,
    PRINCIPAL_HEADER,
    SERVICE_PRINCIPALS,
    acting_headers,
    on_behalf_headers,
    require_acting_identity,
    resolve_acting_identity,
)
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def test_user_principal_is_the_actor():
    identity = resolve_acting_identity("user-123")
    assert identity.principal == identity.actor == "user-123"
    assert identity.on_behalf_of is None
    assert identity.is_service is False


def test_service_may_act_on_behalf_of_a_user():
    identity = resolve_acting_identity("folder-service", "user-123")
    assert identity.principal == "folder-service"
    assert identity.on_behalf_of == "user-123"
    assert identity.actor == "user-123"


def test_a_user_cannot_act_on_behalf_of_anyone():
    identity = resolve_acting_identity("user-123", "someone-else")
    assert identity.actor == "user-123"
    assert identity.on_behalf_of is None


@pytest.mark.parametrize("principal", ["", "   "])
def test_no_principal_resolves_to_none(principal):
    assert resolve_acting_identity(principal, "user-123") is None


def test_require_acting_identity_returns_401_without_principal():
    async def endpoint(request: Request):
        identity = require_acting_identity(request)
        return JSONResponse({"actor": identity.actor, "via": identity.principal})

    async def handle_http(_request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app = Starlette(routes=[Route("/", endpoint)], exception_handlers={HTTPException: handle_http})
    client = TestClient(app)
    assert client.get("/").status_code == 401
    on_behalf = client.get("/", headers=acting_headers("case-service", "user-9"))
    assert on_behalf.json() == {"actor": "user-9", "via": "case-service"}
    spoof = client.get("/", headers={PRINCIPAL_HEADER: "user-1", ON_BEHALF_OF_HEADER: "user-2"})
    assert spoof.json() == {"actor": "user-1", "via": "user-1"}


def test_service_principals_match_the_services_tree():
    services_dir = Path(__file__).resolve().parents[3] / "services"
    if not services_dir.is_dir():
        pytest.skip("services/ not available next to libs/")
    names = {p.name for p in services_dir.iterdir() if (p / "src").is_dir()}
    assert names == SERVICE_PRINCIPALS


def test_on_behalf_headers_only_for_people():
    assert on_behalf_headers("mail-connector", "alice") == {
        PRINCIPAL_HEADER: "mail-connector",
        ON_BEHALF_OF_HEADER: "alice",
    }
    for actor in ["", None, "document-service", "system:retention", "migration-service (von X)"]:
        assert on_behalf_headers("mail-connector", actor) == {PRINCIPAL_HEADER: "mail-connector"}
