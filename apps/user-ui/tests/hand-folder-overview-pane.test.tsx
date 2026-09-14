import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HandFolderOverviewPane } from "@/components/HandFolderOverviewPane";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary, FolderReference, SearchResult } from "@/lib/api";

const listFolderReferencesMock = vi.fn();
const searchDocumentsMock = vi.fn();
const getDocumentMock = vi.fn();
const getFolderMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listFolderReferences: (...args: unknown[]) => listFolderReferencesMock(...args),
    searchDocuments: (...args: unknown[]) => searchDocumentsMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
    getFolder: (...args: unknown[]) => getFolderMock(...args),
  };
});

const REFERENCE: FolderReference = {
  folder_id: "folder-1",
  folder_name: "Handakte Müller",
  document_id: "doc-1",
  document_title: "Rechnung Nr 42",
  added_by: "alice",
  added_at: "2026-01-02T00:00:00Z",
};

const WORK_TRAY_DOC: SearchResult = {
  id: "doc-2",
  title: "Entwurf.txt",
  folder_id: "folder-2",
  folder_name: "Arbeitsvorrat Team A",
  object_type_id: null,
  attributes: {},
  current_version_number: 1,
  deleted_at: null,
  deleted_by: null,
  created_by: "bob",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  retention_until: null,
  full_deletion: false,
  pending_deletion_reason: null,
  registered_at: null,
  classification_level: null,
  rank: 0,
  snippet: "",
} as SearchResult;

const onOpenDocumentMock = vi.fn();
const onOpenFolderMock = vi.fn();

function renderPane() {
  return render(
    <I18nProvider>
      <HandFolderOverviewPane
        token="token-123"
        onOpenDocument={onOpenDocumentMock}
        onOpenFolder={onOpenFolderMock}
      />
    </I18nProvider>
  );
}

describe("HandFolderOverviewPane", () => {
  beforeEach(() => {
    listFolderReferencesMock.mockReset();
    searchDocumentsMock.mockReset();
    getDocumentMock.mockReset();
    getFolderMock.mockReset();
    onOpenDocumentMock.mockReset();
    onOpenFolderMock.mockReset();
  });

  it("shows empty states for both lists when nothing is returned", async () => {
    listFolderReferencesMock.mockResolvedValue({ results: [], total_returned: 0 });
    searchDocumentsMock.mockResolvedValue({ results: [], total_returned: 0, facet_counts: {} });
    renderPane();

    expect(
      await screen.findByText("Keine (für Sie sichtbaren) Hand-Ordner-Referenzen vorhanden.")
    ).toBeInTheDocument();
    expect(screen.getByText("Keine unregistrierten Dokumente vorhanden.")).toBeInTheDocument();
  });

  it("lists a hand-folder reference and opens the referenced document", async () => {
    listFolderReferencesMock.mockResolvedValue({ results: [REFERENCE], total_returned: 1 });
    searchDocumentsMock.mockResolvedValue({ results: [], total_returned: 0, facet_counts: {} });
    getDocumentMock.mockResolvedValue({ id: "doc-1" } as DocumentSummary);
    renderPane();

    await waitFor(() =>
      expect(listFolderReferencesMock).toHaveBeenCalledWith("token-123", { limit: 50 })
    );
    expect(await screen.findByText("Handakte Müller")).toBeInTheDocument();
    expect(screen.getByText("Rechnung Nr 42")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Rechnung Nr 42"));
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledWith("token-123", "doc-1"));
    await waitFor(() =>
      expect(onOpenDocumentMock).toHaveBeenCalledWith({ id: "doc-1" })
    );
  });

  it("opens the referenced folder via the resolve-then-navigate flow", async () => {
    listFolderReferencesMock.mockResolvedValue({ results: [REFERENCE], total_returned: 1 });
    searchDocumentsMock.mockResolvedValue({ results: [], total_returned: 0, facet_counts: {} });
    getFolderMock.mockResolvedValue({ id: "folder-1", name: "Handakte Müller" });
    renderPane();

    fireEvent.click(await screen.findByText("Handakte Müller"));
    await waitFor(() => expect(getFolderMock).toHaveBeenCalledWith("token-123", "folder-1"));
    await waitFor(() => expect(onOpenFolderMock).toHaveBeenCalledWith("folder-1"));
  });

  it("lists an unregistered work-tray document and requests it with registered=false", async () => {
    listFolderReferencesMock.mockResolvedValue({ results: [], total_returned: 0 });
    searchDocumentsMock.mockResolvedValue({
      results: [WORK_TRAY_DOC],
      total_returned: 1,
      facet_counts: {},
    });
    renderPane();

    await waitFor(() =>
      expect(searchDocumentsMock).toHaveBeenCalledWith("token-123", {
        registered: false,
        limit: 50,
      })
    );
    expect(await screen.findByText("Entwurf.txt")).toBeInTheDocument();
    expect(screen.getByText("Arbeitsvorrat Team A")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Entwurf.txt"));
    expect(onOpenDocumentMock).toHaveBeenCalledWith(WORK_TRAY_DOC);
  });
});
