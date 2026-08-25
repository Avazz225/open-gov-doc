import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HandFolderReferencesModal } from "@/components/HandFolderReferencesModal";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary, Folder } from "@/lib/api";

const listFolderDocumentReferencesMock = vi.fn();
const addFolderDocumentReferenceMock = vi.fn();
const removeFolderDocumentReferenceMock = vi.fn();
const getDocumentMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listFolderDocumentReferences: (...args: unknown[]) =>
      listFolderDocumentReferencesMock(...args),
    addFolderDocumentReference: (...args: unknown[]) => addFolderDocumentReferenceMock(...args),
    removeFolderDocumentReference: (...args: unknown[]) =>
      removeFolderDocumentReferenceMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      permissions: [],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const FOLDER: Folder = {
  id: "folder-1",
  name: "Handakte Bauantrag",
  parent_id: "root",
  object_type_id: null,
  attributes: {},
  deleted_at: null,
  deleted_by: null,
  retention_until: null,
  full_deletion: false,
  pending_deletion_reason: null,
};

const DOCUMENT: DocumentSummary = {
  id: "doc-1",
  title: "Anlage 1",
  folder_id: "folder-elsewhere",
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
  registered_at: "2026-01-01T00:00:00Z",
  classification_level: null,
  derivation_type: null,
};

function renderModal(folder: Folder = FOLDER, onClose = vi.fn()) {
  return render(
    <I18nProvider>
      <HandFolderReferencesModal folder={folder} onClose={onClose} />
    </I18nProvider>
  );
}

describe("HandFolderReferencesModal (Post-Roadmap Phase 31 Session 7, ADR 0118)", () => {
  beforeEach(() => {
    listFolderDocumentReferencesMock.mockReset();
    listFolderDocumentReferencesMock.mockResolvedValue([]);
    addFolderDocumentReferenceMock.mockReset();
    removeFolderDocumentReferenceMock.mockReset();
    getDocumentMock.mockReset();
    getDocumentMock.mockResolvedValue(DOCUMENT);
  });

  it("shows the empty state when there are no references yet", async () => {
    renderModal();
    await waitFor(() =>
      expect(listFolderDocumentReferencesMock).toHaveBeenCalledWith("token-123", "folder-1")
    );
    expect(await screen.findByText("Keine Referenzen in dieser Handakte.")).toBeInTheDocument();
  });

  it("adds a reference by document ID and shows its resolved title", async () => {
    addFolderDocumentReferenceMock.mockResolvedValue({
      document_id: "doc-1",
      added_by: "alice",
      added_at: "2026-01-01T00:00:00Z",
      removed_by: null,
      removed_at: null,
      current_version_number: 1,
      document_deleted_at: null,
    });
    listFolderDocumentReferencesMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          document_id: "doc-1",
          added_by: "alice",
          added_at: "2026-01-01T00:00:00Z",
          removed_by: null,
          removed_at: null,
          current_version_number: 1,
          document_deleted_at: null,
        },
      ]);

    renderModal();
    await waitFor(() => expect(listFolderDocumentReferencesMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Dokument-ID"), { target: { value: "doc-1" } });
    fireEvent.click(screen.getByText("Referenz hinzufügen"));

    await waitFor(() =>
      expect(addFolderDocumentReferenceMock).toHaveBeenCalledWith("token-123", "folder-1", {
        documentId: "doc-1",
        addedBy: "alice",
      })
    );
    expect(await screen.findByText("Anlage 1")).toBeInTheDocument();
  });

  it("removes a reference", async () => {
    listFolderDocumentReferencesMock
      .mockResolvedValueOnce([
        {
          document_id: "doc-1",
          added_by: "alice",
          added_at: "2026-01-01T00:00:00Z",
          removed_by: null,
          removed_at: null,
          current_version_number: 1,
          document_deleted_at: null,
        },
      ])
      .mockResolvedValueOnce([]);
    removeFolderDocumentReferenceMock.mockResolvedValue({
      document_id: "doc-1",
      added_by: "alice",
      added_at: "2026-01-01T00:00:00Z",
      removed_by: "alice",
      removed_at: "2026-02-01T00:00:00Z",
      current_version_number: 1,
      document_deleted_at: null,
    });

    renderModal();
    expect(await screen.findByText("Anlage 1")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Entfernen"));

    await waitFor(() =>
      expect(removeFolderDocumentReferenceMock).toHaveBeenCalledWith(
        "token-123",
        "folder-1",
        "doc-1",
        "alice"
      )
    );
    expect(screen.queryByText("Anlage 1")).not.toBeInTheDocument();
  });

  it("shows an unresolved placeholder for a reference the viewer can't resolve", async () => {
    getDocumentMock.mockRejectedValue(new Error("404"));
    listFolderDocumentReferencesMock.mockResolvedValue([
      {
        document_id: "doc-unresolvable",
        added_by: "alice",
        added_at: "2026-01-01T00:00:00Z",
        removed_by: null,
        removed_at: null,
        current_version_number: null,
        document_deleted_at: null,
      },
    ]);

    renderModal();

    expect(
      await screen.findByText("Unbekanntes Dokument (doc-unresolvable)")
    ).toBeInTheDocument();
  });

  it("closes the modal when the close button is clicked", async () => {
    const onClose = vi.fn();
    renderModal(FOLDER, onClose);
    await waitFor(() => expect(listFolderDocumentReferencesMock).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("Schließen"));

    expect(onClose).toHaveBeenCalled();
  });
});
