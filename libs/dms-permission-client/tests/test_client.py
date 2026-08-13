import httpx
from dms_permission_client import (
    PermissionServiceClient,
    RoleAssignmentPendingApprovalError,
    RoleNotFoundError,
)


def _client(handler) -> PermissionServiceClient:
    return PermissionServiceClient(
        "http://permission-service",
        client=httpx.AsyncClient(
            base_url="http://permission-service", transport=httpx.MockTransport(handler)
        ),
    )


async def test_check_returns_allowed_flag():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/check"
        assert request.url.params["principal_id"] == "user-1"
        assert request.url.params["resource_id"] == "folder-1"
        assert request.url.params["permission"] == "folder.read"
        assert request.url.params["access_type"] == "read"
        return httpx.Response(200, json={"allowed": True})

    result = await _client(handler).check(
        principal_id="user-1", resource_id="folder-1", permission="folder.read"
    )

    assert result is True


async def test_check_passes_through_write_access_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["access_type"] == "write"
        return httpx.Response(200, json={"allowed": False})

    result = await _client(handler).check(
        principal_id="user-1", resource_id="doc-1", permission="document.write", access_type="write"
    )

    assert result is False


async def test_check_batch_returns_empty_dict_without_request_for_empty_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not perform a request for an empty resource_ids list")

    result = await _client(handler).check_batch(
        principal_id="user-1", permission="document.read", resource_ids=[]
    )

    assert result == {}


async def test_check_batch_posts_ids_and_returns_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/check/batch"
        return httpx.Response(200, json={"results": {"doc-1": True, "doc-2": False}})

    result = await _client(handler).check_batch(
        principal_id="user-1", permission="document.read", resource_ids=["doc-1", "doc-2"]
    )

    assert result == {"doc-1": True, "doc-2": False}


async def test_has_permission_checks_root_effective_permissions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/effective-permissions/user-1/root"
        return httpx.Response(200, json={"permissions": ["admin.user_management"]})

    result = await _client(handler).has_permission("user-1", "admin.user_management")

    assert result is True


async def test_get_role_id_finds_role_by_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/roles"
        return httpx.Response(
            200, json=[{"id": 1, "name": "domain-admin-users"}, {"id": 2, "name": "other"}]
        )

    result = await _client(handler).get_role_id("domain-admin-users")

    assert result == 1


async def test_get_role_id_returns_none_when_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    result = await _client(handler).get_role_id("no-such-role")

    assert result is None


async def test_ensure_role_assignment_raises_when_role_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/roles"
        return httpx.Response(200, json=[])

    try:
        await _client(handler).ensure_role_assignment(principal_id="user-1", role_name="ghost")
        raise AssertionError("expected RoleNotFoundError")
    except RoleNotFoundError:
        pass


async def test_ensure_role_assignment_is_a_noop_when_already_assigned():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/roles":
            return httpx.Response(200, json=[{"id": 1, "name": "domain-admin-users"}])
        if request.url.path == "/role-assignments" and request.method == "GET":
            return httpx.Response(200, json=[{"role_id": 1, "resource_id": "root"}])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    await _client(handler).ensure_role_assignment(
        principal_id="user-1", role_name="domain-admin-users"
    )

    assert ("POST", "/role-assignments") not in calls


async def test_ensure_role_assignment_creates_when_missing_and_immediately_effective():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/roles":
            return httpx.Response(200, json=[{"id": 1, "name": "domain-admin-users"}])
        if request.url.path == "/role-assignments" and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path == "/role-assignments" and request.method == "POST":
            return httpx.Response(201, json={"status": "created"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    await _client(handler).ensure_role_assignment(
        principal_id="user-1", role_name="domain-admin-users"
    )


async def test_ensure_role_assignment_raises_when_pending_approval():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/roles":
            return httpx.Response(200, json=[{"id": 1, "name": "domain-admin-users"}])
        if request.url.path == "/role-assignments" and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path == "/role-assignments" and request.method == "POST":
            return httpx.Response(201, json={"status": "pending_approval"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    try:
        await _client(handler).ensure_role_assignment(
            principal_id="user-1", role_name="domain-admin-users"
        )
        raise AssertionError("expected RoleAssignmentPendingApprovalError")
    except RoleAssignmentPendingApprovalError:
        pass


async def test_close_closes_the_underlying_httpx_client():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    client = _client(handler)
    await client.close()

    assert client._client.is_closed
