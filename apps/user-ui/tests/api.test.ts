import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, listChildFolders, login } from "@/lib/api";

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const { ok = true, status = 200 } = init;
  return vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: "",
    json: async () => body,
    blob: async () => new Blob([JSON.stringify(body)]),
  });
}

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("login posts credentials to the gateway's auth-service route", async () => {
    const fetchMock = mockFetchOnce({
      access_token: "a",
      refresh_token: "r",
      expires_in: 300,
      token_type: "Bearer",
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await login("alice", "secret");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8009/api/auth-service/login",
      expect.objectContaining({ method: "POST" })
    );
    expect(result.access_token).toBe("a");
  });

  it("attaches the bearer token for authenticated calls", async () => {
    const fetchMock = mockFetchOnce([]);
    vi.stubGlobal("fetch", fetchMock);

    await listChildFolders("token-123", "root");

    const [, init] = fetchMock.mock.calls[0];
    const headers = init.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-123");
  });

  it("raises ApiError with the backend's detail message on failure", async () => {
    const fetchMock = mockFetchOnce({ detail: "Kein aktiver Dienst" }, { ok: false, status: 503 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("alice", "wrong")).rejects.toMatchObject(
      new ApiError(503, "Kein aktiver Dienst")
    );
  });
});
