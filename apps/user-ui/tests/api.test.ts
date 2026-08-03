import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createFolder,
  getKennzeichenConfig,
  listChildFolders,
  login,
  updateDocumentMetadata,
} from "@/lib/api";

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

  it("creates a folder via the folder-service route", async () => {
    const fetchMock = mockFetchOnce({ id: "f1", name: "Neu" });
    vi.stubGlobal("fetch", fetchMock);

    await createFolder("token-123", { name: "Neu", parentId: "root", createdBy: "alice" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8009/api/folder-service/folders");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      name: "Neu",
      parent_id: "root",
      created_by: "alice",
      object_type_id: null,
    });
  });

  it("includes the chosen folder class (Ordnerklasse) when creating a folder", async () => {
    const fetchMock = mockFetchOnce({ id: "f1", name: "Neu" });
    vi.stubGlobal("fetch", fetchMock);

    await createFolder("token-123", {
      name: "Neu",
      parentId: "root",
      createdBy: "alice",
      objectTypeId: 5,
    });

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({
      name: "Neu",
      parent_id: "root",
      created_by: "alice",
      object_type_id: 5,
    });
  });

  it("PATCHes document metadata via the document-service route", async () => {
    const fetchMock = mockFetchOnce({ id: "d1", title: "Neu" });
    vi.stubGlobal("fetch", fetchMock);

    await updateDocumentMetadata("token-123", "d1", { title: "Neu", attributes: { a: 1 } });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8009/api/document-service/documents/d1");
    expect(init.method).toBe("PATCH");
  });

  it("fetches the global kennzeichen display config via the object-type-service route", async () => {
    const fetchMock = mockFetchOnce({
      show_before_filename: true,
      updated_at: "2026-01-01T00:00:00Z",
    });
    vi.stubGlobal("fetch", fetchMock);

    const config = await getKennzeichenConfig("token-123");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8009/api/object-type-service/kennzeichen-config",
      expect.anything()
    );
    expect(config.show_before_filename).toBe(true);
  });

  it("raises ApiError with the backend's detail message on failure", async () => {
    const fetchMock = mockFetchOnce({ detail: "Kein aktiver Dienst" }, { ok: false, status: 503 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("alice", "wrong")).rejects.toMatchObject(
      new ApiError(503, "Kein aktiver Dienst")
    );
  });
});
