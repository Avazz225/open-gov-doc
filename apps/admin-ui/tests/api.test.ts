import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createRole, listUsers, login, setGatewayBaseUrl } from "@/lib/api";

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = init;
  return vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: "",
    json: async () => body,
  });
}

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setGatewayBaseUrl("http://localhost:8009");
  });

  it("routes requests through the actively configured gateway base URL", async () => {
    setGatewayBaseUrl("http://second-installation:8009");
    const fetchMock = mockFetchOnce([]);
    vi.stubGlobal("fetch", fetchMock);

    await listUsers("token-123");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("http://second-installation:8009/api/auth-service/users");
  });

  it("login posts credentials to the gateway's auth-service route", async () => {
    const fetchMock = mockFetchOnce({
      access_token: "a",
      refresh_token: "r",
      expires_in: 300,
      token_type: "Bearer",
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await login("admin", "secret");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8009/api/auth-service/login",
      expect.objectContaining({ method: "POST" })
    );
    expect(result.access_token).toBe("a");
  });

  it("attaches the bearer token when listing users", async () => {
    const fetchMock = mockFetchOnce([]);
    vi.stubGlobal("fetch", fetchMock);

    await listUsers("token-123");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8009/api/auth-service/users");
    const headers = init.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-123");
  });

  it("createRole posts to the permission-service route", async () => {
    const fetchMock = mockFetchOnce({ id: 1, name: "Viewer", description: "", permissions: [] });
    vi.stubGlobal("fetch", fetchMock);

    await createRole("token-123", { name: "Viewer", description: "", permissions: ["read"] });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8009/api/permission-service/roles");
  });

  it("raises ApiError with the backend's detail message on failure", async () => {
    const fetchMock = mockFetchOnce({ detail: "Ungültiger Token" }, { ok: false, status: 401 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(listUsers("bad-token")).rejects.toMatchObject(
      new ApiError(401, "Ungültiger Token")
    );
  });
});
