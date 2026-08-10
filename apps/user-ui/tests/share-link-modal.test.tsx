import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShareLinkModal } from "@/components/ShareLinkModal";
import { I18nProvider } from "@/i18n";

const listShareLinksMock = vi.fn();
const createShareLinkMock = vi.fn();
const revokeShareLinkMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listShareLinks: (...args: unknown[]) => listShareLinksMock(...args),
    createShareLink: (...args: unknown[]) => createShareLinkMock(...args),
    revokeShareLink: (...args: unknown[]) => revokeShareLinkMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

function renderModal(onClose = vi.fn()) {
  return render(
    <I18nProvider>
      <ShareLinkModal documentId="doc-1" documentTitle="Vertrag.pdf" onClose={onClose} />
    </I18nProvider>
  );
}

describe("ShareLinkModal", () => {
  beforeEach(() => {
    listShareLinksMock.mockReset();
    listShareLinksMock.mockResolvedValue([]);
    createShareLinkMock.mockReset();
    revokeShareLinkMock.mockReset();
  });

  it("shows an empty state when the document has no share links yet", async () => {
    renderModal();
    expect(await screen.findByText("Noch keine Freigabelinks für dieses Dokument.")).toBeInTheDocument();
  });

  it("lists an active link with its share URL and lets it be revoked", async () => {
    const farFuture = new Date(Date.now() + 86400000).toISOString();
    listShareLinksMock.mockResolvedValue([
      {
        token: "tok-abc",
        document_id: "doc-1",
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
        expires_at: farFuture,
        revoked_at: null,
        revoked_by: null,
      },
    ]);

    renderModal();

    const urlInput = await screen.findByLabelText("Freigabe-URL");
    expect((urlInput as HTMLInputElement).value).toContain("token=tok-abc");

    revokeShareLinkMock.mockResolvedValue(undefined);
    listShareLinksMock.mockResolvedValueOnce([
      {
        token: "tok-abc",
        document_id: "doc-1",
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
        expires_at: farFuture,
        revoked_at: "2026-02-01T00:00:00Z",
        revoked_by: "alice",
      },
    ]);
    fireEvent.click(screen.getByText("Widerrufen"));

    await waitFor(() => expect(revokeShareLinkMock).toHaveBeenCalledWith("token-123", "tok-abc"));
  });

  it("creates a new share link with the chosen expiry date", async () => {
    createShareLinkMock.mockResolvedValue({
      token: "tok-new",
      document_id: "doc-1",
      created_by: "alice",
      created_at: "2026-01-01T00:00:00Z",
      expires_at: "2026-01-08T00:00:00Z",
      revoked_at: null,
      revoked_by: null,
    });

    renderModal();
    await waitFor(() => expect(listShareLinksMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Gültig bis"), { target: { value: "2026-01-08" } });
    fireEvent.click(screen.getByText("Link erzeugen"));

    await waitFor(() =>
      expect(createShareLinkMock).toHaveBeenCalledWith(
        "token-123",
        "doc-1",
        new Date("2026-01-08").toISOString()
      )
    );
  });

  it("closes the modal when the close button is clicked", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    await waitFor(() => expect(listShareLinksMock).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("Schließen"));

    expect(onClose).toHaveBeenCalled();
  });
});
