import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecordsQuarantineOverviewPane } from "@/components/RecordsQuarantineOverviewPane";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary, RecordsQuarantine } from "@/lib/api";

const listRecordsQuarantineMock = vi.fn();
const releaseRecordsQuarantineMock = vi.fn();
const getDocumentMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listRecordsQuarantine: (...args: unknown[]) => listRecordsQuarantineMock(...args),
    releaseRecordsQuarantine: (...args: unknown[]) => releaseRecordsQuarantineMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
      permissions: ["admin.records_quarantine"],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const ENTRY: RecordsQuarantine = {
  id: "q1",
  document_id: "doc-1",
  reason: "Prüfung",
  auto_delete_at: null,
  set_by: "alice",
  set_at: "2026-01-01T00:00:00Z",
  released_by: null,
  released_at: null,
};

const onOpenDocumentMock = vi.fn();

function renderPane() {
  return render(
    <I18nProvider>
      <RecordsQuarantineOverviewPane token="token-123" onOpenDocument={onOpenDocumentMock} />
    </I18nProvider>
  );
}

describe("RecordsQuarantineOverviewPane", () => {
  beforeEach(() => {
    listRecordsQuarantineMock.mockReset();
    releaseRecordsQuarantineMock.mockReset();
    getDocumentMock.mockReset();
    onOpenDocumentMock.mockReset();
  });

  it("requests the installation-wide active listing with no document_id filter", async () => {
    listRecordsQuarantineMock.mockResolvedValue([]);
    renderPane();

    await waitFor(() =>
      expect(listRecordsQuarantineMock).toHaveBeenCalledWith("token-123", undefined, true)
    );
    expect(await screen.findByText("Aktuell ist kein Dokument unter Quarantäne.")).toBeInTheDocument();
  });

  it("lists an active quarantine entry with the resolved document title", async () => {
    listRecordsQuarantineMock.mockResolvedValue([ENTRY]);
    getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Rechnung Nr 42" } as DocumentSummary);
    renderPane();

    expect(await screen.findByText("Rechnung Nr 42")).toBeInTheDocument();
    expect(screen.getByText(/gesetzt von alice/)).toBeInTheDocument();
    expect(screen.getByText(/Prüfung/)).toBeInTheDocument();
  });

  it("shows a placeholder when the referenced document can no longer be resolved", async () => {
    listRecordsQuarantineMock.mockResolvedValue([ENTRY]);
    getDocumentMock.mockRejectedValue(new Error("404"));
    renderPane();

    expect(await screen.findByText("Dokument nicht mehr abrufbar")).toBeInTheDocument();
  });

  it("opens the referenced document via onOpenDocument", async () => {
    listRecordsQuarantineMock.mockResolvedValue([ENTRY]);
    getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Rechnung Nr 42" } as DocumentSummary);
    renderPane();

    fireEvent.click(await screen.findByText("Rechnung Nr 42"));

    await waitFor(() =>
      expect(onOpenDocumentMock).toHaveBeenCalledWith({ id: "doc-1", title: "Rechnung Nr 42" })
    );
  });

  it("releases the quarantine and reloads the list", async () => {
    listRecordsQuarantineMock.mockResolvedValueOnce([ENTRY]).mockResolvedValueOnce([]);
    getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Rechnung Nr 42" } as DocumentSummary);
    releaseRecordsQuarantineMock.mockResolvedValue({ ...ENTRY, released_by: "admin" });
    renderPane();

    await screen.findByText("Rechnung Nr 42");
    fireEvent.click(screen.getByRole("button", { name: "Quarantäne aufheben" }));

    await waitFor(() =>
      expect(releaseRecordsQuarantineMock).toHaveBeenCalledWith("token-123", "q1", "admin")
    );
    expect(await screen.findByText("Aktuell ist kein Dokument unter Quarantäne.")).toBeInTheDocument();
  });
});
