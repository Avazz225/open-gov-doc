"""P84-S2: the hub pages GET /installations (default 100, ordered by id).
FederationHubClient.list_installations must walk every page, and
get_installation must resolve one installation by id without the list."""

import httpx
from workflow_service.federation_client import INSTALLATIONS_PAGE_SIZE, FederationHubClient

TOTAL = INSTALLATIONS_PAGE_SIZE * 2 + 37
ALL = [{"id": f"inst-{i:04d}"} for i in range(TOTAL)]


def _hub(calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/installations":
            limit = int(request.url.params["limit"])
            offset = int(request.url.params["offset"])
            return httpx.Response(200, json=ALL[offset : offset + limit])
        installation_id = path.removeprefix("/installations/")
        match = next((i for i in ALL if i["id"] == installation_id), None)
        return httpx.Response(200, json=match) if match else httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _client(calls: list[str]) -> FederationHubClient:
    client = FederationHubClient("http://hub.local")
    client._client = httpx.AsyncClient(base_url="http://hub.local", transport=_hub(calls))
    return client


async def test_list_installations_walks_every_page():
    calls: list[str] = []
    client = _client(calls)
    installations = await client.list_installations()
    await client.close()

    assert [i["id"] for i in installations] == [i["id"] for i in ALL]
    assert len(calls) == 3  # two full pages and the short last one


async def test_get_installation_resolves_a_target_beyond_the_first_page():
    calls: list[str] = []
    client = _client(calls)
    late = await client.get_installation(ALL[-1]["id"])
    missing = await client.get_installation("no-such-installation")
    await client.close()

    assert late == ALL[-1]
    assert missing is None
    assert len(calls) == 2  # one GET per lookup, never the list
