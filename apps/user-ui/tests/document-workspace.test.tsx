import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentWorkspace } from "@/components/DocumentWorkspace";
import { I18nProvider } from "@/i18n";

function renderWorkspace() {
  return render(
    <I18nProvider>
      <DocumentWorkspace />
    </I18nProvider>
  );
}

const listChildFoldersMock = vi.fn();
const listDocumentsInFolderMock = vi.fn();
const downloadDocumentMock = vi.fn();
const uploadDocumentMock = vi.fn();
const createFolderMock = vi.fn();
const renameFolderMock = vi.fn();
const deleteFolderMock = vi.fn();
const getObjectTypeMock = vi.fn();
const updateDocumentMetadataMock = vi.fn();
const listRenditionsMock = vi.fn();
const downloadRenditionContentMock = vi.fn();
const listOcrResultsMock = vi.fn();
const downloadOcrPageImageMock = vi.fn();
const listDocumentVersionsMock = vi.fn();
const downloadDocumentVersionMock = vi.fn();
const getSearchFacetsMock = vi.fn();
const searchDocumentsMock = vi.fn();
const listObjectTypesMock = vi.fn();
const getObjectTypeLayoutMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listChildFolders: (...args: unknown[]) => listChildFoldersMock(...args),
  listDocumentsInFolder: (...args: unknown[]) => listDocumentsInFolderMock(...args),
  downloadDocument: (...args: unknown[]) => downloadDocumentMock(...args),
  uploadDocument: (...args: unknown[]) => uploadDocumentMock(...args),
  createFolder: (...args: unknown[]) => createFolderMock(...args),
  renameFolder: (...args: unknown[]) => renameFolderMock(...args),
  deleteFolder: (...args: unknown[]) => deleteFolderMock(...args),
  getObjectType: (...args: unknown[]) => getObjectTypeMock(...args),
  listObjectTypes: (...args: unknown[]) => listObjectTypesMock(...args),
  getObjectTypeLayout: (...args: unknown[]) => getObjectTypeLayoutMock(...args),
  updateDocumentMetadata: (...args: unknown[]) => updateDocumentMetadataMock(...args),
  listRenditions: (...args: unknown[]) => listRenditionsMock(...args),
  downloadRenditionContent: (...args: unknown[]) => downloadRenditionContentMock(...args),
  listOcrResults: (...args: unknown[]) => listOcrResultsMock(...args),
  downloadOcrPageImage: (...args: unknown[]) => downloadOcrPageImageMock(...args),
  listDocumentVersions: (...args: unknown[]) => listDocumentVersionsMock(...args),
  downloadDocumentVersion: (...args: unknown[]) => downloadDocumentVersionMock(...args),
  getSearchFacets: (...args: unknown[]) => getSearchFacetsMock(...args),
  searchDocuments: (...args: unknown[]) => searchDocumentsMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const document1 = {
  id: "d1",
  title: "Rechnung.pdf",
  folder_id: "root",
  object_type_id: null,
  attributes: {},
  current_version_number: 1,
  deleted_at: null,
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("DocumentWorkspace", () => {
  beforeEach(() => {
    listChildFoldersMock.mockReset();
    listDocumentsInFolderMock.mockReset();
    downloadDocumentMock.mockReset();
    uploadDocumentMock.mockReset();
    createFolderMock.mockReset();
    renameFolderMock.mockReset();
    deleteFolderMock.mockReset();
    getObjectTypeMock.mockReset();
    updateDocumentMetadataMock.mockReset();
    listRenditionsMock.mockReset();
    listRenditionsMock.mockResolvedValue([]);
    downloadRenditionContentMock.mockReset();
    listOcrResultsMock.mockReset();
    listOcrResultsMock.mockResolvedValue([]);
    downloadOcrPageImageMock.mockReset();
    listDocumentVersionsMock.mockReset();
    listDocumentVersionsMock.mockResolvedValue([
      {
        version_number: 1,
        filename: "Rechnung.pdf",
        content_type: "application/pdf",
        size_bytes: 1,
        checksum_sha256: "x",
        is_conflict: false,
        based_on_version_number: null,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadDocumentVersionMock.mockReset();
    getSearchFacetsMock.mockReset();
    getSearchFacetsMock.mockResolvedValue({ object_types: [] });
    searchDocumentsMock.mockReset();
    searchDocumentsMock.mockResolvedValue({
      results: [],
      total_returned: 0,
      facet_counts: { folder: [], object_type: [] },
    });
    listObjectTypesMock.mockReset();
    listObjectTypesMock.mockResolvedValue([]);
    getObjectTypeLayoutMock.mockReset();
    // Standardmäßig kein konfiguriertes Layout - MetadataPanel/SearchPane/
    // UploadForm fallen dann auf ihr jeweiliges Fallback-Verhalten zurück
    // (siehe SearchPane.tsx `fallbackLayout`), damit bestehende Tests ohne
    // Layout-Bezug unverändert funktionieren.
    getObjectTypeLayoutMock.mockRejectedValue(new Error("no layout mocked for this test"));
    window.localStorage.clear();
  });

  it("lists subfolders and documents of the current folder", async () => {
    listChildFoldersMock.mockResolvedValue([
      { id: "f1", name: "Verträge", parent_id: "root", object_type_id: null, attributes: {} },
    ]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);

    renderWorkspace();

    expect(await screen.findByText(/Verträge/)).toBeInTheDocument();
    expect(screen.getByText(/Rechnung.pdf/)).toBeInTheDocument();
    expect(listChildFoldersMock).toHaveBeenCalledWith("token-123", "root");
  });

  it("navigates into a subfolder and updates the breadcrumb", async () => {
    listChildFoldersMock.mockImplementation(async (_token: string, folderId: string) => {
      if (folderId === "root") {
        return [
          { id: "f1", name: "Verträge", parent_id: "root", object_type_id: null, attributes: {} },
        ];
      }
      return [];
    });
    listDocumentsInFolderMock.mockResolvedValue([]);

    const user = userEvent.setup();
    renderWorkspace();

    const folderButton = await screen.findByText(/Verträge/);
    await user.click(folderButton);

    await waitFor(() => expect(listChildFoldersMock).toHaveBeenCalledWith("token-123", "f1"));
    const breadcrumbs = screen.getByLabelText("Ordnerpfad");
    expect(within(breadcrumbs).getByText("Verträge")).toBeInTheDocument();
    expect(within(breadcrumbs).getByText("Start")).toBeInTheDocument();
  });

  it("opens a document as a tab and syncs the preview pane", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listRenditionsMock.mockResolvedValue([]);

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const tabBar = screen.getByRole("tablist");
    expect(within(tabBar).getByText("Rechnung.pdf")).toBeInTheDocument();
    const previewPane = screen.getByLabelText("Vorschau");
    expect(within(previewPane).getByText("Rechnung.pdf")).toBeInTheDocument();
    expect(listRenditionsMock).toHaveBeenCalledWith("token-123", "d1", 1);
    expect(
      await screen.findByText(/Für dieses Dokument liegt noch keine Vorschau vor/)
    ).toBeInTheDocument();
  });

  it("shows the rendered thumbnail when a ready rendition exists", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listRenditionsMock.mockResolvedValue([
      {
        id: "d1:1:thumbnail",
        document_id: "d1",
        version_number: 1,
        rendition_type: "thumbnail",
        source_filename: "Rechnung.pdf",
        source_content_type: "application/pdf",
        target_filename: "Rechnung_thumbnail.png",
        target_content_type: "image/png",
        size_bytes: 123,
        status: "ready",
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadRenditionContentMock.mockResolvedValue(new Blob(["fake-png"], { type: "image/png" }));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    await waitFor(() =>
      expect(downloadRenditionContentMock).toHaveBeenCalledWith("token-123", "d1:1:thumbnail")
    );
    const previewPane = screen.getByLabelText("Vorschau");
    const image = await within(previewPane).findByRole("img");
    expect(image).toHaveAttribute("src", "blob:mock-url");
  });

  it("renders OCR word spans with percentage-based positioning when a ready OCR result exists", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listRenditionsMock.mockResolvedValue([
      {
        id: "d1:1:thumbnail",
        document_id: "d1",
        version_number: 1,
        rendition_type: "thumbnail",
        source_filename: "Rechnung.pdf",
        source_content_type: "application/pdf",
        target_filename: "Rechnung_thumbnail.png",
        target_content_type: "image/png",
        size_bytes: 123,
        status: "ready",
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadRenditionContentMock.mockResolvedValue(new Blob(["fake-png"], { type: "image/png" }));
    listOcrResultsMock.mockResolvedValue([
      {
        id: "d1:1",
        document_id: "d1",
        version_number: 1,
        status: "ready",
        engine: "native_text_layer",
        average_confidence: 100.0,
        full_text: "Hallo",
        pages: [
          {
            page_number: 1,
            width: 200,
            height: 100,
            words: [{ text: "Hallo", left: 20, top: 10, width: 40, height: 20, confidence: 95 }],
          },
        ],
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadOcrPageImageMock.mockRejectedValue(new Error("409"));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const previewPane = screen.getByLabelText("Vorschau");
    const word = await within(previewPane).findByText("Hallo");
    expect(word).toHaveClass("ocr-word");
    expect(word).toHaveStyle({ left: "10%", top: "10%", width: "20%", height: "20%" });
  });

  it("does not render an OCR overlay when no ready OCR result exists", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listRenditionsMock.mockResolvedValue([
      {
        id: "d1:1:thumbnail",
        document_id: "d1",
        version_number: 1,
        rendition_type: "thumbnail",
        source_filename: "Rechnung.pdf",
        source_content_type: "application/pdf",
        target_filename: "Rechnung_thumbnail.png",
        target_content_type: "image/png",
        size_bytes: 123,
        status: "ready",
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadRenditionContentMock.mockResolvedValue(new Blob(["fake-png"], { type: "image/png" }));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const previewPane = screen.getByLabelText("Vorschau");
    await within(previewPane).findByRole("img");
    expect(previewPane.querySelectorAll(".ocr-word")).toHaveLength(0);
  });

  it("lets the user pick an older version and reloads renditions/OCR for it", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listDocumentVersionsMock.mockResolvedValue([
      {
        version_number: 1,
        filename: "Rechnung.pdf",
        content_type: "application/pdf",
        size_bytes: 1,
        checksum_sha256: "x",
        is_conflict: false,
        based_on_version_number: null,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        version_number: 2,
        filename: "Rechnung-v2.pdf",
        content_type: "application/pdf",
        size_bytes: 2,
        checksum_sha256: "y",
        is_conflict: false,
        based_on_version_number: 1,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-02T00:00:00Z",
      },
    ]);

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));
    await waitFor(() => expect(listRenditionsMock).toHaveBeenCalledWith("token-123", "d1", 1));

    await user.selectOptions(screen.getByLabelText("Version auswählen"), "2");

    await waitFor(() => expect(listRenditionsMock).toHaveBeenCalledWith("token-123", "d1", 2));
    expect(listOcrResultsMock).toHaveBeenCalledWith("token-123", "d1", 2);
  });

  it("downloads the selected version's content, not just the current version", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listDocumentVersionsMock.mockResolvedValue([
      {
        version_number: 1,
        filename: "Rechnung.pdf",
        content_type: "application/pdf",
        size_bytes: 1,
        checksum_sha256: "x",
        is_conflict: false,
        based_on_version_number: null,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        version_number: 2,
        filename: "Rechnung-v2.pdf",
        content_type: "application/pdf",
        size_bytes: 2,
        checksum_sha256: "y",
        is_conflict: false,
        based_on_version_number: 1,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-02T00:00:00Z",
      },
    ]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["content"]));

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));
    await waitFor(() => expect(listRenditionsMock).toHaveBeenCalledWith("token-123", "d1", 1));
    await user.selectOptions(screen.getByLabelText("Version auswählen"), "2");
    await waitFor(() => expect(listRenditionsMock).toHaveBeenCalledWith("token-123", "d1", 2));

    const previewPane = screen.getByLabelText("Vorschau");
    await user.click(within(previewPane).getByText("Herunterladen"));

    await waitFor(() =>
      expect(downloadDocumentVersionMock).toHaveBeenCalledWith("token-123", "d1", 2)
    );
  });

  it("creates a new folder and reloads the listing", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    createFolderMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(listChildFoldersMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByText("Neuer Ordner"));
    await user.type(screen.getByPlaceholderText("Ordnername"), "Neuer Bereich");
    await user.click(screen.getByText("Anlegen"));

    await waitFor(() =>
      expect(createFolderMock).toHaveBeenCalledWith("token-123", {
        name: "Neuer Bereich",
        parentId: "root",
        createdBy: "alice",
      })
    );
    await waitFor(() => expect(listChildFoldersMock).toHaveBeenCalledTimes(2));
  });

  it("deletes a folder after confirmation", async () => {
    listChildFoldersMock.mockResolvedValue([
      { id: "f1", name: "Alt", parent_id: "root", object_type_id: null, attributes: {} },
    ]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    deleteFolderMock.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByLabelText("Alt löschen"));

    await waitFor(() => expect(deleteFolderMock).toHaveBeenCalledWith("token-123", "f1"));
  });

  it("saves edited metadata and updates the open tab's title", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    updateDocumentMetadataMock.mockResolvedValue({ ...document1, title: "Rechnung-2026.pdf" });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const metadataPanel = screen.getByLabelText("Metadaten");
    const titleInput = within(metadataPanel).getByLabelText("Titel");
    await user.clear(titleInput);
    await user.type(titleInput, "Rechnung-2026.pdf");
    fireEvent.submit(within(metadataPanel).getByRole("form", { name: "Dokumentmetadaten bearbeiten" }));

    await waitFor(() =>
      expect(updateDocumentMetadataMock).toHaveBeenCalledWith("token-123", "d1", {
        title: "Rechnung-2026.pdf",
        attributes: {},
      })
    );
    const tabBar = screen.getByRole("tablist");
    await waitFor(() => expect(within(tabBar).getByText("Rechnung-2026.pdf")).toBeInTheDocument());
  });

  it("reloads the document list after a successful upload", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    uploadDocumentMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(listDocumentsInFolderMock).toHaveBeenCalledTimes(1));
    await user.click(screen.getByText("Hochladen"));

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const fileInput = screen.getByLabelText("Datei");
    await user.upload(fileInput, file);
    fireEvent.submit(screen.getByRole("form", { name: "Dokument hochladen" }));

    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalled());
    await waitFor(() => expect(listDocumentsInFolderMock).toHaveBeenCalledTimes(2));
  });

  it("switches to the search view via the icon rail and back to documents", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);

    const user = userEvent.setup();
    renderWorkspace();

    await screen.findByText(/Rechnung.pdf/);
    expect(screen.queryByLabelText("Suche")).not.toBeInTheDocument();

    await user.click(screen.getByTitle("Suche"));
    expect(screen.getByLabelText("Suche")).toBeInTheDocument();
    expect(screen.queryByText("Neuer Ordner")).not.toBeInTheDocument();

    await user.click(screen.getByTitle("Dokumente"));
    expect(screen.queryByLabelText("Suche")).not.toBeInTheDocument();
    expect(await screen.findByText(/Rechnung.pdf/)).toBeInTheDocument();
  });

  it("runs a search and opens a result as a document tab", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    searchDocumentsMock.mockResolvedValue({
      results: [
        {
          id: "d2",
          title: "Suchtreffer.pdf",
          folder_id: "root",
          object_type_id: null,
          attributes: {},
          current_version_number: 1,
          deleted_at: null,
          created_by: "alice",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          folder_name: "Start",
          rank: 0.5,
          snippet: "...gefundener Text...",
        },
      ],
      total_returned: 1,
      facet_counts: { folder: [], object_type: [] },
    });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByTitle("Suche"));
    await user.type(screen.getByLabelText("Suchbegriff"), "Vertrag");
    await user.click(screen.getByText("Suchen"));

    await waitFor(() => expect(searchDocumentsMock).toHaveBeenCalled());
    expect(searchDocumentsMock.mock.calls[0][1].q).toBe("Vertrag");

    await user.click(await screen.findByText("Suchtreffer.pdf"));

    // Öffnen bleibt bewusst in der Suchansicht (kein automatischer
    // View-Wechsel, siehe SearchPane-Kommentar) - die Vorschau rechts
    // reagiert trotzdem sofort, da sie unabhängig vom aktiven View immer
    // vom activeTabId gesteuert wird.
    const previewPane = screen.getByLabelText("Vorschau");
    expect(within(previewPane).getByText("Suchtreffer.pdf")).toBeInTheDocument();
  });

  it("shows attribute filter controls for the selected object type", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    getSearchFacetsMock.mockResolvedValue({
      object_types: [
        {
          id: 1,
          name: "Rechnung",
          attributes: [
            { name: "kunde", type: "string" },
            { name: "faelligkeit", type: "date" },
          ],
        },
      ],
    });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByTitle("Suche"));
    await user.selectOptions(await screen.findByLabelText("Objekttyp"), "1");

    expect(screen.getByText("kunde")).toBeInTheDocument();
    expect(screen.getByText("faelligkeit")).toBeInTheDocument();
    expect(screen.getByLabelText("faelligkeit von")).toBeInTheDocument();
    expect(screen.getByLabelText("faelligkeit bis")).toBeInTheDocument();
  });

  it("shows a no-results message when the search returns nothing", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByTitle("Suche"));
    await user.click(screen.getByText("Suchen"));

    expect(await screen.findByText("Keine Treffer.")).toBeInTheDocument();
  });

  it("shows the folder's class icon and toggles between list and tree view", async () => {
    listChildFoldersMock.mockImplementation(async (_token: string, folderId: string) => {
      if (folderId === "root") {
        return [{ id: "f1", name: "Projekte", parent_id: "root", object_type_id: 5, attributes: {} }];
      }
      return [];
    });
    listDocumentsInFolderMock.mockResolvedValue([]);
    listObjectTypesMock.mockImplementation(async (_token: string, appliesTo?: string) => {
      if (appliesTo === "folder") {
        return [
          { id: 5, name: "Projektordner", applies_to: "folder", attributes: [], icon: "folder-star" },
        ];
      }
      return [];
    });

    const user = userEvent.setup();
    renderWorkspace();

    expect(await screen.findByText(/⭐ Projekte/)).toBeInTheDocument();

    await user.click(screen.getByText("Baum"));
    expect(screen.getByLabelText("Ordner-Baumansicht")).toBeInTheDocument();
    expect(screen.queryByLabelText("Ordnerpfad")).not.toBeInTheDocument();
    expect(await screen.findByText(/⭐ Projekte/)).toBeInTheDocument();
  });

  it("navigates via the tree view to a nested folder and rebuilds the breadcrumb in list view", async () => {
    listChildFoldersMock.mockImplementation(async (_token: string, folderId: string) => {
      if (folderId === "root") {
        return [{ id: "a", name: "A", parent_id: "root", object_type_id: null, attributes: {} }];
      }
      if (folderId === "a") {
        return [{ id: "b", name: "B", parent_id: "a", object_type_id: null, attributes: {} }];
      }
      return [];
    });
    listDocumentsInFolderMock.mockResolvedValue([]);

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByText("Baum"));
    await user.click(await screen.findByLabelText("A aufklappen"));
    await user.click(await screen.findByText(/📁 B/));

    await user.click(screen.getByText("Liste"));

    const breadcrumbs = await screen.findByLabelText("Ordnerpfad");
    expect(within(breadcrumbs).getByText("Start")).toBeInTheDocument();
    expect(within(breadcrumbs).getByText("A")).toBeInTheDocument();
    expect(within(breadcrumbs).getByText("B")).toBeInTheDocument();
    expect(listChildFoldersMock).toHaveBeenCalledWith("token-123", "b");
  });

  it("renders metadata fields via the display layout with its labels and required flags", async () => {
    const typedDocument = { ...document1, object_type_id: 7 };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([typedDocument]);
    getObjectTypeMock.mockResolvedValue({
      id: 7,
      name: "Rechnung",
      applies_to: "document",
      attributes: [{ name: "Betrag", type: "decimal", required: true }],
      icon: null,
    });
    getObjectTypeLayoutMock.mockImplementation(async (_token: string, id: number, purpose: string) => {
      if (id === 7 && purpose === "display") {
        return {
          rows: [{ columns: [{ attribute: "Betrag", label: "Rechnungsbetrag", required: true }] }],
          responsive_breakpoint_px: 600,
          is_custom: true,
        };
      }
      throw new Error("unexpected layout request");
    });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const metadataPanel = screen.getByLabelText("Metadaten");
    expect(await within(metadataPanel).findByText(/Rechnungsbetrag/)).toBeInTheDocument();
    expect(within(metadataPanel).getByLabelText(/Rechnungsbetrag/)).toHaveAttribute("type", "number");
  });

  it("orders and labels search filters via the object type's search layout instead of raw attribute order", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    getSearchFacetsMock.mockResolvedValue({
      object_types: [
        {
          id: 3,
          name: "Rechnung",
          attributes: [
            { name: "kunde", type: "string" },
            { name: "faelligkeit", type: "date" },
          ],
        },
      ],
    });
    getObjectTypeLayoutMock.mockImplementation(async (_token: string, id: number, purpose: string) => {
      if (id === 3 && purpose === "search") {
        return {
          rows: [{ columns: [{ attribute: "faelligkeit", label: "Fällig am", required: false }] }],
          responsive_breakpoint_px: 600,
          is_custom: true,
        };
      }
      throw new Error("unexpected layout request");
    });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByTitle("Suche"));
    await user.selectOptions(await screen.findByLabelText("Objekttyp"), "3");

    expect(await screen.findByText("Fällig am")).toBeInTheDocument();
    expect(screen.queryByText("kunde")).not.toBeInTheDocument();
  });

  it("uploads a document with a selected object type and its upload-layout attribute values", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    uploadDocumentMock.mockResolvedValue({});
    listObjectTypesMock.mockImplementation(async (_token: string, appliesTo?: string) => {
      if (appliesTo === "document") {
        return [
          {
            id: 9,
            name: "Vertrag",
            applies_to: "document",
            attributes: [{ name: "Partner", type: "string", required: true }],
            icon: null,
          },
        ];
      }
      return [];
    });
    getObjectTypeLayoutMock.mockImplementation(async (_token: string, id: number, purpose: string) => {
      if (id === 9 && purpose === "upload") {
        return {
          rows: [{ columns: [{ attribute: "Partner", label: "Vertragspartner", required: true }] }],
          responsive_breakpoint_px: 600,
          is_custom: false,
        };
      }
      throw new Error("unexpected layout request");
    });

    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(listDocumentsInFolderMock).toHaveBeenCalledTimes(1));
    await user.click(screen.getByText("Hochladen"));

    await user.selectOptions(await screen.findByLabelText("Objekttyp"), "9");

    const file = new File(["hello"], "vertrag.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Datei"), file);
    await user.type(await screen.findByLabelText(/Vertragspartner/), "Beispiel GmbH");

    fireEvent.submit(screen.getByRole("form", { name: "Dokument hochladen" }));

    await waitFor(() =>
      expect(uploadDocumentMock).toHaveBeenCalledWith(
        "token-123",
        expect.objectContaining({
          objectTypeId: 9,
          attributes: { Partner: "Beispiel GmbH" },
        })
      )
    );
  });
});
