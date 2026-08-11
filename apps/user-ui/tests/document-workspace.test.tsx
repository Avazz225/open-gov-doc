import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentWorkspace } from "@/components/DocumentWorkspace";
import { I18nProvider } from "@/i18n";
import type { Folder } from "@/lib/api";

function renderWorkspace() {
  return render(
    <I18nProvider>
      <DocumentWorkspace />
    </I18nProvider>
  );
}

// Seit P16-S1 (dockbarer Arbeitsbereich, `dockview-react`) trägt JEDE
// dockview-Gruppe selbst ein `role="region"`-Element mit demselben
// `aria-label` wie ihr aktives Panel (z. B. "Metadaten") - zusätzlich zu der
// eigentlichen `<section aria-label="Metadaten">` aus `MetadataPanel`/
// `ExplorerPane` selbst. `getByLabelText` findet dadurch für feste
// Panel-Titel (die nicht wie `PreviewPane` je Dokument eindeutig gemacht
// wurden) immer zwei Treffer - dieser Helper filtert gezielt auf das
// eigene `<section>`-Element.
function getPaneSectionByLabel(name: string): HTMLElement {
  const matches = screen.getAllByLabelText(name);
  const section = matches.find((el) => el.tagName === "SECTION");
  if (!section) throw new Error(`Kein <section>-Element mit Label "${name}" gefunden`);
  return section;
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
const getKennzeichenConfigMock = vi.fn();
const listSignaturesMock = vi.fn();
const createSignatureMock = vi.fn();
const verifySignatureMock = vi.fn();
const putDocumentRetentionMock = vi.fn();
const restoreDocumentMock = vi.fn();
const listDeletedDocumentsMock = vi.fn();
const listLegalHoldsMock = vi.fn();
const createLegalHoldMock = vi.fn();
const releaseLegalHoldMock = vi.fn();
const trashFolderMock = vi.fn();
const restoreFolderMock = vi.fn();
const listDeletedFoldersMock = vi.fn();
const putFolderRetentionMock = vi.fn();
const listFolderLegalHoldsMock = vi.fn();
const createFolderLegalHoldMock = vi.fn();
const releaseFolderLegalHoldMock = vi.fn();
const trashDocumentMock = vi.fn();
const getApprovalConfigMock = vi.fn();
const listApprovalRequestsMock = vi.fn();
const approveApprovalRequestMock = vi.fn();
const rejectApprovalRequestMock = vi.fn();
const listFavoritesMock = vi.fn();
const addFavoriteMock = vi.fn();
const removeFavoriteMock = vi.fn();
const getDocumentMock = vi.fn();
const getFolderMock = vi.fn();
const getShareLinkConfigMock = vi.fn();
const updateFolderAttributesMock = vi.fn();
const listDeletedDocumentsGlobalMock = vi.fn();
const listDeletedFoldersGlobalMock = vi.fn();
const purgeDocumentMock = vi.fn();
const purgeFolderMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listChildFolders: (...args: unknown[]) => listChildFoldersMock(...args),
  listDocumentsInFolder: (...args: unknown[]) => listDocumentsInFolderMock(...args),
  downloadDocument: (...args: unknown[]) => downloadDocumentMock(...args),
  uploadDocument: (...args: unknown[]) => uploadDocumentMock(...args),
  createFolder: (...args: unknown[]) => createFolderMock(...args),
  renameFolder: (...args: unknown[]) => renameFolderMock(...args),
  deleteFolder: (...args: unknown[]) => deleteFolderMock(...args),
  trashFolder: (...args: unknown[]) => trashFolderMock(...args),
  restoreFolder: (...args: unknown[]) => restoreFolderMock(...args),
  listDeletedFolders: (...args: unknown[]) => listDeletedFoldersMock(...args),
  putFolderRetention: (...args: unknown[]) => putFolderRetentionMock(...args),
  listFolderLegalHolds: (...args: unknown[]) => listFolderLegalHoldsMock(...args),
  createFolderLegalHold: (...args: unknown[]) => createFolderLegalHoldMock(...args),
  releaseFolderLegalHold: (...args: unknown[]) => releaseFolderLegalHoldMock(...args),
  trashDocument: (...args: unknown[]) => trashDocumentMock(...args),
  getApprovalConfig: (...args: unknown[]) => getApprovalConfigMock(...args),
  listApprovalRequests: (...args: unknown[]) => listApprovalRequestsMock(...args),
  approveApprovalRequest: (...args: unknown[]) => approveApprovalRequestMock(...args),
  rejectApprovalRequest: (...args: unknown[]) => rejectApprovalRequestMock(...args),
  listFavorites: (...args: unknown[]) => listFavoritesMock(...args),
  addFavorite: (...args: unknown[]) => addFavoriteMock(...args),
  removeFavorite: (...args: unknown[]) => removeFavoriteMock(...args),
  getDocument: (...args: unknown[]) => getDocumentMock(...args),
  getFolder: (...args: unknown[]) => getFolderMock(...args),
  getShareLinkConfig: (...args: unknown[]) => getShareLinkConfigMock(...args),
  updateFolderAttributes: (...args: unknown[]) => updateFolderAttributesMock(...args),
  listDeletedDocumentsGlobal: (...args: unknown[]) => listDeletedDocumentsGlobalMock(...args),
  listDeletedFoldersGlobal: (...args: unknown[]) => listDeletedFoldersGlobalMock(...args),
  purgeDocument: (...args: unknown[]) => purgeDocumentMock(...args),
  purgeFolder: (...args: unknown[]) => purgeFolderMock(...args),
  getObjectType: (...args: unknown[]) => getObjectTypeMock(...args),
  listObjectTypes: (...args: unknown[]) => listObjectTypesMock(...args),
  getObjectTypeLayout: (...args: unknown[]) => getObjectTypeLayoutMock(...args),
  getKennzeichenConfig: (...args: unknown[]) => getKennzeichenConfigMock(...args),
  updateDocumentMetadata: (...args: unknown[]) => updateDocumentMetadataMock(...args),
  listRenditions: (...args: unknown[]) => listRenditionsMock(...args),
  downloadRenditionContent: (...args: unknown[]) => downloadRenditionContentMock(...args),
  listOcrResults: (...args: unknown[]) => listOcrResultsMock(...args),
  downloadOcrPageImage: (...args: unknown[]) => downloadOcrPageImageMock(...args),
  listDocumentVersions: (...args: unknown[]) => listDocumentVersionsMock(...args),
  downloadDocumentVersion: (...args: unknown[]) => downloadDocumentVersionMock(...args),
  getSearchFacets: (...args: unknown[]) => getSearchFacetsMock(...args),
  searchDocuments: (...args: unknown[]) => searchDocumentsMock(...args),
  listSignatures: (...args: unknown[]) => listSignaturesMock(...args),
  createSignature: (...args: unknown[]) => createSignatureMock(...args),
  verifySignature: (...args: unknown[]) => verifySignatureMock(...args),
  putDocumentRetention: (...args: unknown[]) => putDocumentRetentionMock(...args),
  restoreDocument: (...args: unknown[]) => restoreDocumentMock(...args),
  listDeletedDocuments: (...args: unknown[]) => listDeletedDocumentsMock(...args),
  listLegalHolds: (...args: unknown[]) => listLegalHoldsMock(...args),
  createLegalHold: (...args: unknown[]) => createLegalHoldMock(...args),
  releaseLegalHold: (...args: unknown[]) => releaseLegalHoldMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

// Veränderlich statt einer festen Rolle, damit Tests zur Kennzeichen-
// Rollenprüfung (P5e-S3) `realm_roles` gezielt setzen können - `vi.hoisted`
// nötig, da die `vi.mock`-Factory unten früher ausgeführt wird als normale
// `const`/`let`-Deklarationen im Modul.
const { authState } = vi.hoisted(() => ({ authState: { realmRoles: [] as string[] } }));

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "alice", email: null, realm_roles: authState.realmRoles },
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
  deleted_by: null,
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
    listSignaturesMock.mockReset();
    listSignaturesMock.mockResolvedValue([]);
    createSignatureMock.mockReset();
    verifySignatureMock.mockReset();
    putDocumentRetentionMock.mockReset();
    restoreDocumentMock.mockReset();
    listDeletedDocumentsMock.mockReset();
    listDeletedDocumentsMock.mockResolvedValue([]);
    listLegalHoldsMock.mockReset();
    listLegalHoldsMock.mockResolvedValue([]);
    createLegalHoldMock.mockReset();
    releaseLegalHoldMock.mockReset();
    trashFolderMock.mockReset();
    restoreFolderMock.mockReset();
    listDeletedFoldersMock.mockReset();
    listDeletedFoldersMock.mockResolvedValue([]);
    putFolderRetentionMock.mockReset();
    listFolderLegalHoldsMock.mockReset();
    listFolderLegalHoldsMock.mockResolvedValue([]);
    createFolderLegalHoldMock.mockReset();
    releaseFolderLegalHoldMock.mockReset();
    trashDocumentMock.mockReset();
    getApprovalConfigMock.mockReset();
    getApprovalConfigMock.mockResolvedValue({
      action_type: "folder.delete",
      requires_approval: false,
      required_permission: null,
      updated_at: "2026-01-01T00:00:00Z",
    });
    listApprovalRequestsMock.mockReset();
    listApprovalRequestsMock.mockResolvedValue([]);
    approveApprovalRequestMock.mockReset();
    rejectApprovalRequestMock.mockReset();
    listFavoritesMock.mockReset();
    listFavoritesMock.mockResolvedValue([]);
    addFavoriteMock.mockReset();
    removeFavoriteMock.mockReset();
    getDocumentMock.mockReset();
    getFolderMock.mockReset();
    updateFolderAttributesMock.mockReset();
    listDeletedDocumentsGlobalMock.mockReset();
    listDeletedDocumentsGlobalMock.mockResolvedValue([]);
    listDeletedFoldersGlobalMock.mockReset();
    listDeletedFoldersGlobalMock.mockResolvedValue([]);
    purgeDocumentMock.mockReset();
    purgeFolderMock.mockReset();
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
    getKennzeichenConfigMock.mockReset();
    getKennzeichenConfigMock.mockResolvedValue({
      show_before_filename: true,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getShareLinkConfigMock.mockReset();
    getShareLinkConfigMock.mockResolvedValue({
      enabled: false,
      max_validity_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
    authState.realmRoles = [];
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

    // dockview rendert seit P16-S1 pro Panel-Gruppe eine eigene Tableiste
    // (Explorer/Vorschau/Metadaten je eigenständig, siehe
    // `DockableDocumentArea`) - `getByRole("tablist")` wäre daher nicht mehr
    // eindeutig. Der Dokument-Tab selbst hat aber einen eindeutigen
    // barrierefreien Namen (den Dokumenttitel).
    expect(screen.getByRole("tab", { name: "Rechnung.pdf" })).toBeInTheDocument();
    const previewPane = screen.getByLabelText("Vorschau: Rechnung.pdf");
    expect(within(previewPane).getByText("Rechnung.pdf")).toBeInTheDocument();
    expect(listRenditionsMock).toHaveBeenCalledWith("token-123", "d1", 1);
    expect(
      await screen.findByText(/Für dieses Dokument liegt noch keine Vorschau vor/)
    ).toBeInTheDocument();
  });

  describe("Dockbarer Arbeitsbereich (dockview, P16-S1)", () => {
    const document2 = { ...document1, id: "d9", title: "Zweitdokument.pdf" };

    it("opens a second document as another tab in the same preview group", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([document1, document2]);
      listRenditionsMock.mockResolvedValue([]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByText(/Rechnung.pdf/));
      await user.click(await screen.findByText(/Zweitdokument.pdf/));

      // Beide Dokumente sind als eigene Tabs derselben dockview-Gruppe
      // vorhanden und per Ziehen in eine eigene Gruppe abspaltbar ("mehrere
      // Dokumente gleichzeitig sicht- und anordenbar", Konzept 8 - dockview
      // selbst übernimmt das Splitten/Andocken, siehe ADR 0057). Nur der
      // gerade aktive Tab rendert seinen Inhalt (dockviews eigenes,
      // Browser-Tab-artiges Standardverhalten je Gruppe) - erst ein
      // tatsächliches Abspalten in eine zweite Gruppe zeigt beide
      // gleichzeitig, das lässt sich in jsdom (kein echtes Drag&Drop) nicht
      // sinnvoll nachstellen.
      expect(screen.getByRole("tab", { name: "Rechnung.pdf" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Zweitdokument.pdf" })).toBeInTheDocument();
      expect(screen.getByLabelText("Vorschau: Zweitdokument.pdf")).toBeInTheDocument();
    });

    it("removes a document from state when its dockview tab is closed", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([document1]);
      listRenditionsMock.mockResolvedValue([]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByText(/Rechnung.pdf/));
      expect(screen.getByRole("tab", { name: "Rechnung.pdf" })).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Close Rechnung.pdf" }));

      expect(screen.queryByRole("tab", { name: "Rechnung.pdf" })).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Vorschau: Rechnung.pdf")).not.toBeInTheDocument();
    });

    it("resets the layout via the toolbar button without losing open documents", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([document1]);
      listRenditionsMock.mockResolvedValue([]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByText(/Rechnung.pdf/));
      expect(screen.getByRole("tab", { name: "Rechnung.pdf" })).toBeInTheDocument();

      await user.click(screen.getByText("Standardanordnung"));

      // Die Anordnung wurde neu aufgebaut, das offene Dokument bleibt aber
      // erhalten - "Zurücksetzen" setzt nur das Layout zurück, nicht die
      // offenen Dokumente. Der Tab (von dockview selbst aus dem internen
      // Modell gerendert) ist das zuverlässigste DOM-Signal; zusätzlich wird
      // das persistierte Layout direkt geprüft, da jsdom (kein echtes
      // Requestanimationframe/Portal-Reconciliation) das erneute Rendern des
      // Vorschau-Panelinhalts bei einer INNERHALB DESSELBEN Ticks
      // wiederverwendeten Panel-ID ("doc:d1" existierte vor dem Reset schon
      // einmal) nicht zuverlässig nachvollzieht, obwohl dockviews eigenes
      // Modell nachweislich korrekt ist (siehe ADR 0057 "Offene Punkte") -
      // in einem echten Browser tritt das nicht auf (Live-Verifikation).
      expect(screen.getByRole("tab", { name: "Rechnung.pdf" })).toBeInTheDocument();
      await waitFor(() => {
        const stored = window.localStorage.getItem("dms.workspace.dockLayout");
        expect(stored).not.toBeNull();
        expect(JSON.parse(stored ?? "{}").panels).toHaveProperty("doc:d1");
      });
    });

    it("persists the dockview layout to localStorage after a change", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([document1]);
      listRenditionsMock.mockResolvedValue([]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByText(/Rechnung.pdf/));

      await waitFor(() => {
        const stored = window.localStorage.getItem("dms.workspace.dockLayout");
        expect(stored).not.toBeNull();
        expect(JSON.parse(stored ?? "{}")).toHaveProperty("grid");
      });
    });
  });

  it("shows an image document in full resolution via its raw bytes, not a thumbnail rendition (2.4, Nutzer-Feedback)", async () => {
    const imageDocument = { ...document1, id: "d5", title: "foto.jpg" };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([imageDocument]);
    listDocumentVersionsMock.mockResolvedValue([
      {
        version_number: 1,
        filename: "foto.jpg",
        content_type: "image/jpeg",
        size_bytes: 123,
        checksum_sha256: "j",
        is_conflict: false,
        based_on_version_number: null,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["fake-jpeg"], { type: "image/jpeg" }));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/foto.jpg/));

    await waitFor(() =>
      expect(downloadDocumentVersionMock).toHaveBeenCalledWith("token-123", "d5", 1)
    );
    const previewPane = screen.getByLabelText("Vorschau: foto.jpg");
    const image = await within(previewPane).findByRole("img");
    expect(image).toHaveAttribute("src", "blob:mock-url");
    expect(downloadRenditionContentMock).not.toHaveBeenCalled();
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
        engine: "tesseract",
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

    const previewPane = screen.getByLabelText("Vorschau: Rechnung.pdf");
    const word = await within(previewPane).findByText("Hallo");
    expect(word).toHaveClass("ocr-word");
    expect(word).toHaveStyle({ left: "10%", top: "10%", width: "20%", height: "20%" });
  });

  it("does not render an OCR overlay when no ready OCR result exists", async () => {
    const imageDocument = { ...document1, id: "d5", title: "foto.jpg" };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([imageDocument]);
    listDocumentVersionsMock.mockResolvedValue([
      {
        version_number: 1,
        filename: "foto.jpg",
        content_type: "image/jpeg",
        size_bytes: 123,
        checksum_sha256: "j",
        is_conflict: false,
        based_on_version_number: null,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["fake-jpeg"], { type: "image/jpeg" }));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/foto.jpg/));

    const previewPane = screen.getByLabelText("Vorschau: foto.jpg");
    await within(previewPane).findByRole("img");
    expect(previewPane.querySelectorAll(".ocr-word")).toHaveLength(0);
  });

  it("shows a substitute_text rendition as a text preview (DOCX/PPTX/ODS)", async () => {
    const docxDocument = { ...document1, id: "d4", title: "vertrag.docx" };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([docxDocument]);
    listDocumentVersionsMock.mockResolvedValue([
      {
        version_number: 1,
        filename: "vertrag.docx",
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes: 5,
        checksum_sha256: "d",
        is_conflict: false,
        based_on_version_number: null,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    listRenditionsMock.mockResolvedValue([
      {
        id: "d4:1:substitute",
        document_id: "d4",
        version_number: 1,
        rendition_type: "substitute_text",
        source_filename: "vertrag.docx",
        source_content_type:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        target_filename: "vertrag.txt",
        target_content_type: "text/plain; charset=utf-8",
        size_bytes: 20,
        status: "ready",
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadRenditionContentMock.mockResolvedValue(
      new Blob(["Vertragsinhalt als Text"], { type: "text/plain" })
    );

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/vertrag.docx/));

    const previewPane = screen.getByLabelText("Vorschau: vertrag.docx");
    expect(await within(previewPane).findByText("Vertragsinhalt als Text")).toBeInTheDocument();
    expect(downloadRenditionContentMock).toHaveBeenCalledWith("token-123", "d4:1:substitute");
    expect(listOcrResultsMock).not.toHaveBeenCalled();
  });

  it("lets a multi-page PDF's page be switched, reloading only the page image", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock.mockResolvedValue([
      {
        id: "d1:1",
        document_id: "d1",
        version_number: 1,
        status: "ready",
        engine: "tesseract",
        average_confidence: 100.0,
        full_text: "Seite 1 Seite 2",
        pages: [
          { page_number: 1, width: 200, height: 100, words: [] },
          { page_number: 2, width: 200, height: 100, words: [] },
        ],
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadOcrPageImageMock.mockResolvedValue(new Blob(["fake-png"], { type: "image/png" }));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const previewPane = screen.getByLabelText("Vorschau: Rechnung.pdf");
    await within(previewPane).findByRole("img");
    await waitFor(() => expect(downloadOcrPageImageMock).toHaveBeenCalledWith("token-123", "d1:1", 1));

    await user.selectOptions(within(previewPane).getByLabelText("Seite auswählen"), "2");

    await waitFor(() => expect(downloadOcrPageImageMock).toHaveBeenCalledWith("token-123", "d1:1", 2));
    // Ein Seitenwechsel lädt nur das Seitenbild neu, nicht erneut Renditionen/OCR-Ergebnisse.
    expect(listRenditionsMock).toHaveBeenCalledTimes(1);
    expect(listOcrResultsMock).toHaveBeenCalledTimes(1);
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

    const previewPane = screen.getByLabelText("Vorschau: Rechnung.pdf");
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

  it("lets a folder class be chosen when creating a folder", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    createFolderMock.mockResolvedValue({});
    listObjectTypesMock.mockImplementation(async (_token: string, appliesTo?: string) => {
      if (appliesTo === "folder") {
        return [
          { id: 5, name: "Projektordner", applies_to: "folder", attributes: [], icon: null },
        ];
      }
      return [];
    });

    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(listChildFoldersMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByText("Neuer Ordner"));
    await user.type(screen.getByPlaceholderText("Ordnername"), "Projekt A");
    await user.selectOptions(screen.getByLabelText("Ordnerklasse"), "5");
    await user.click(screen.getByText("Anlegen"));

    await waitFor(() =>
      expect(createFolderMock).toHaveBeenCalledWith("token-123", {
        name: "Projekt A",
        parentId: "root",
        createdBy: "alice",
        objectTypeId: 5,
      })
    );
  });

  it("moves a folder to the trash after confirmation via the context menu (5.2, seit P7-S1c)", async () => {
    listChildFoldersMock.mockResolvedValue([
      { id: "f1", name: "Alt", parent_id: "root", object_type_id: null, attributes: {} },
    ]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    trashFolderMock.mockResolvedValue({
      status: "trashed",
      folder: { id: "f1", name: "Alt", parent_id: "root", object_type_id: null, attributes: {} },
      approval_request_id: null,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderWorkspace();

    const folderNameButton = await screen.findByText(/Alt/);
    fireEvent.contextMenu(folderNameButton.closest("li")!);
    await user.click(await screen.findByText("Alt löschen"));

    await waitFor(() => expect(trashFolderMock).toHaveBeenCalledWith("token-123", "f1", "alice"));
  });

  it("shows a pending-approval message instead of deleting when folder.delete requires approval (5.2, seit P7-S1c)", async () => {
    listChildFoldersMock.mockResolvedValue([
      { id: "f1", name: "Alt", parent_id: "root", object_type_id: null, attributes: {} },
    ]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    getApprovalConfigMock.mockImplementation(async (_token: string, actionType: string) => ({
      action_type: actionType,
      requires_approval: actionType === "folder.delete",
      required_permission: null,
      updated_at: "2026-01-01T00:00:00Z",
    }));
    trashFolderMock.mockResolvedValue({
      status: "pending_approval",
      folder: null,
      approval_request_id: "req-1",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderWorkspace();

    const folderNameButton = await screen.findByText(/Alt/);
    fireEvent.contextMenu(folderNameButton.closest("li")!);
    await user.click(await screen.findByText('Löschung für "Alt" beantragen'));

    expect(
      await screen.findByText(
        "Löschantrag gestellt - wartet auf Genehmigung durch eine zweite Person."
      )
    ).toBeInTheDocument();
  });

  it("shows delete capability for documents via the context menu (5.2, seit P7-S1c)", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    trashDocumentMock.mockResolvedValue({
      status: "trashed",
      document: { ...document1, deleted_at: "2026-01-05T00:00:00Z" },
      approval_request_id: null,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderWorkspace();

    const documentButton = await screen.findByText(/Rechnung.pdf/);
    fireEvent.contextMenu(documentButton.closest("li")!);
    await user.click(await screen.findByText('"Rechnung.pdf" löschen'));

    await waitFor(() =>
      expect(trashDocumentMock).toHaveBeenCalledWith("token-123", "d1", "alice")
    );
  });

  it("adds a document to favorites via the context menu, showing the ⭐ marker (seit P7-S1d)", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    listFavoritesMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          id: "fav-1",
          user_id: "alice",
          object_type: "document",
          object_id: "d1",
          created_at: "2026-01-01T00:00:00Z",
        },
      ]);
    addFavoriteMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderWorkspace();

    const documentButton = await screen.findByText(/Rechnung.pdf/);
    fireEvent.contextMenu(documentButton.closest("li")!);
    await user.click(await screen.findByText('"Rechnung.pdf" zu Favoriten hinzufügen'));

    await waitFor(() =>
      expect(addFavoriteMock).toHaveBeenCalledWith("token-123", {
        user_id: "alice",
        object_type: "document",
        object_id: "d1",
      })
    );
    await waitFor(() => {
      const button = screen.getByText(/Rechnung\.pdf/).closest("button");
      expect(button?.textContent).toContain("⭐");
    });
  });

  it("opens a favorited folder from the Favoriten view and rebuilds the breadcrumb (seit P7-S1d)", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    listFavoritesMock.mockResolvedValue([
      {
        id: "fav-1",
        user_id: "alice",
        object_type: "folder",
        object_id: "b",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    getFolderMock.mockImplementation(async (_token: string, folderId: string) => {
      if (folderId === "b") {
        return { id: "b", name: "B", parent_id: "a", object_type_id: null, attributes: {} };
      }
      if (folderId === "a") {
        return { id: "a", name: "A", parent_id: "root", object_type_id: null, attributes: {} };
      }
      throw new Error(`unexpected folder id ${folderId}`);
    });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByTitle("Favoriten"));
    // Seit P16-S1 bleibt der (ausgeblendete) Explorer inkl. seines
    // "Baum"-Umschalters immer gemountet (siehe oben) - ein ungescoptes
    // `/B/` würde ihn ebenfalls treffen. Auf die Favoriten-Pane eingrenzen.
    const favoritesPane = screen.getByLabelText("Favoriten");
    await user.click(await within(favoritesPane).findByText(/B/));
    await user.click(screen.getByText("Öffnen"));

    const breadcrumbs = await screen.findByLabelText("Ordnerpfad");
    expect(within(breadcrumbs).getByText("Start")).toBeInTheDocument();
    expect(within(breadcrumbs).getByText("A")).toBeInTheDocument();
    expect(within(breadcrumbs).getByText("B")).toBeInTheDocument();
  });

  it("shows the trash toggle and restores a deleted document (5.2, seit P7-S1)", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    const deletedDoc = { ...document1, id: "d-trash", title: "Altvertrag.pdf", deleted_at: "2026-01-05T00:00:00Z" };
    listDeletedDocumentsMock.mockResolvedValue([deletedDoc]);
    restoreDocumentMock.mockResolvedValue({ ...deletedDoc, deleted_at: null });

    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(listChildFoldersMock).toHaveBeenCalledTimes(1));
    expect(listDeletedDocumentsMock).not.toHaveBeenCalled();

    await user.click(screen.getByText("Papierkorb anzeigen"));

    await waitFor(() =>
      expect(listDeletedDocumentsMock).toHaveBeenCalledWith("token-123", "root")
    );
    expect(await screen.findByText(/Altvertrag.pdf/)).toBeInTheDocument();

    await user.click(screen.getByText("Wiederherstellen"));

    await waitFor(() =>
      expect(restoreDocumentMock).toHaveBeenCalledWith("token-123", "d-trash")
    );
  });

  it("shows deleted folders in the trash and restores them (5.2, seit P7-S1b)", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    const deletedFolder: Folder = {
      id: "f-trash",
      name: "Alte Akte",
      parent_id: "root",
      object_type_id: null,
      attributes: {},
      deleted_at: "2026-01-05T00:00:00Z",
      deleted_by: "alice",
      retention_until: null,
      full_deletion: false,
      pending_deletion_reason: null,
    };
    listDeletedFoldersMock.mockResolvedValue([deletedFolder]);
    restoreFolderMock.mockResolvedValue({ ...deletedFolder, deleted_at: null });

    const user = userEvent.setup();
    renderWorkspace();
    await waitFor(() => expect(listChildFoldersMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByText("Papierkorb anzeigen"));

    await waitFor(() =>
      expect(listDeletedFoldersMock).toHaveBeenCalledWith("token-123", "root")
    );
    expect(await screen.findByText(/Alte Akte/)).toBeInTheDocument();

    await user.click(screen.getByText("Wiederherstellen"));

    await waitFor(() => expect(restoreFolderMock).toHaveBeenCalledWith("token-123", "f-trash"));
  });

  it("opens the folder retention modal and saves a retention date (5.2, seit P7-S1b)", async () => {
    listChildFoldersMock.mockResolvedValue([
      {
        id: "f1",
        name: "Alt",
        parent_id: "root",
        object_type_id: null,
        attributes: {},
        deleted_at: null,
        retention_until: null,
        full_deletion: false,
        pending_deletion_reason: null,
      },
    ]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    putFolderRetentionMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByLabelText("Aufbewahrung für Alt"));
    await waitFor(() =>
      expect(listFolderLegalHoldsMock).toHaveBeenCalledWith("token-123", "f1", true)
    );

    fireEvent.change(screen.getByLabelText("Aufbewahren bis"), {
      target: { value: "2026-12-31" },
    });
    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() =>
      expect(putFolderRetentionMock).toHaveBeenCalledWith("token-123", "f1", {
        retentionUntil: new Date("2026-12-31").toISOString(),
        fullDeletion: false,
        reason: null,
      })
    );
  });

  it("saves edited metadata and updates the open tab's title", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([document1]);
    updateDocumentMetadataMock.mockResolvedValue({ ...document1, title: "Rechnung-2026.pdf" });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const metadataPanel = getPaneSectionByLabel("Metadaten");
    // dockview verhält sich wie ein echter Editor: eine Panelgruppe muss erst
    // fokussiert/aktiviert werden ("Klick hinein"), bevor Elemente darin
    // interaktiv fokussierbar sind - ohne diesen Klick lehnt userEvent
    // `.clear()`/`.type()` mit "could not be focused" ab.
    await user.click(metadataPanel);
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
    // Bestätigt zugleich, dass `DockableDocumentArea.handleMetadataSaved`
    // den dockview-Tab-Titel per `panel.api.setTitle()` aktualisiert, siehe
    // dort.
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Rechnung-2026.pdf" })).toBeInTheDocument()
    );
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

  it("accepts a dropped file via drag-and-drop in the upload modal", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    uploadDocumentMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(listDocumentsInFolderMock).toHaveBeenCalledTimes(1));
    await user.click(screen.getByText("Hochladen"));

    const dialog = screen.getByRole("dialog", { name: "Dokument hochladen" });
    const file = new File(["hello"], "gedroppt.txt", { type: "text/plain" });
    fireEvent.drop(dialog, { dataTransfer: { files: [file] } });

    expect(await screen.findByText("gedroppt.txt")).toBeInTheDocument();

    fireEvent.submit(screen.getByRole("form", { name: "Dokument hochladen" }));

    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalled());
  });

  it("closes the upload modal via the cancel button without uploading", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);

    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(listDocumentsInFolderMock).toHaveBeenCalledTimes(1));
    await user.click(screen.getByText("Hochladen"));
    expect(screen.getByRole("dialog", { name: "Dokument hochladen" })).toBeInTheDocument();

    await user.click(screen.getByText("Abbrechen"));

    expect(screen.queryByRole("dialog", { name: "Dokument hochladen" })).not.toBeInTheDocument();
    expect(uploadDocumentMock).not.toHaveBeenCalled();
  });

  it("renders a client-side text preview for text/plain documents instead of an image rendition", async () => {
    const textDocument = { ...document1, id: "d3", title: "notiz.txt" };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([textDocument]);
    listDocumentVersionsMock.mockResolvedValue([
      {
        version_number: 1,
        filename: "notiz.txt",
        content_type: "text/plain",
        size_bytes: 11,
        checksum_sha256: "z",
        is_conflict: false,
        based_on_version_number: null,
        comment: null,
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["Hallo Welt"], { type: "text/plain" }));

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/notiz.txt/));

    const previewPane = screen.getByLabelText("Vorschau: notiz.txt");
    expect(await within(previewPane).findByText("Hallo Welt")).toBeInTheDocument();
    expect(listRenditionsMock).not.toHaveBeenCalled();
    expect(listOcrResultsMock).not.toHaveBeenCalled();
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
    // Seit P16-S1 bleibt `DockableDocumentArea` (inkl. Explorer) beim
    // Wechsel zu einem IconRail-Sonderbereich gemountet - nur per CSS
    // ausgeblendet (das dockview-Layout/offene Dokumente sollen einen
    // Sonderbereichs-Ausflug überstehen, siehe dort). "Neuer Ordner" bleibt
    // also im DOM, ist aber nicht mehr sichtbar.
    expect(screen.getByText("Neuer Ordner")).not.toBeVisible();

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
    expect(screen.getByText(/Näherungssuche/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Suchbegriff"), "Vertrag");
    await user.click(screen.getByText("Suchen"));

    await waitFor(() => expect(searchDocumentsMock).toHaveBeenCalled());
    expect(searchDocumentsMock.mock.calls[0][1].q).toBe("Vertrag");

    await user.click(await screen.findByText("Suchtreffer.pdf"));

    // Seit P16-S1 schaltet das Öffnen eines Dokuments (aus jedem
    // Sonderbereich heraus, nicht nur der Suche) automatisch auf die
    // Dokumentenansicht um - vorher blieb ein aus der Suche geöffnetes
    // Dokument unbemerkt im Hintergrund offen, da die Vorschau nur innerhalb
    // der Dokumentenansicht sichtbar war (siehe `DocumentWorkspace.
    // openDocumentTab`).
    expect(screen.queryByLabelText("Suche")).not.toBeInTheDocument();
    const previewPane = screen.getByLabelText("Vorschau: Suchtreffer.pdf");
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

    const metadataPanel = getPaneSectionByLabel("Metadaten");
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

  it("prefixes a document's title with its Kennzeichen when the global default allows it", async () => {
    const kennzeichenDoc = {
      ...document1,
      id: "d2",
      title: "Vertrag.pdf",
      object_type_id: 7,
      attributes: { Kennzeichen: "2026-001" },
    };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([kennzeichenDoc]);
    listObjectTypesMock.mockImplementation(async (_token: string, appliesTo?: string) => {
      if (appliesTo === "document") {
        return [
          {
            id: 7,
            name: "Vertrag",
            applies_to: "document",
            attributes: [],
            icon: null,
            kennzeichen_display_override: null,
          },
        ];
      }
      return [];
    });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/2026-001 Vertrag\.pdf/));

    // Seit P16-S1 hat jede dockview-Panelgruppe (Explorer/Vorschau/
    // Metadaten) ihre eigene Tableiste - `getByRole("tablist")` ist daher
    // nicht mehr eindeutig, siehe oben. Der dockview-Tab selbst zeigt
    // bewusst den rohen Dokumenttitel ohne Kennzeichen-Präfix (siehe
    // `DockableDocumentArea` - dieselbe Vereinfachung, die auch die
    // Vorschau-Überschrift schon immer hatte); die Kennzeichen-Formatierung
    // bleibt der Explorer-Dateiliste vorbehalten, dort weiterhin bestätigt.
    expect(screen.getByRole("tab", { name: "Vertrag.pdf" })).toBeInTheDocument();
  });

  it("hides the Kennzeichen prefix when the object type overrides the global default off", async () => {
    const kennzeichenDoc = {
      ...document1,
      id: "d2",
      title: "Vertrag.pdf",
      object_type_id: 7,
      attributes: { Kennzeichen: "2026-001" },
    };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([kennzeichenDoc]);
    listObjectTypesMock.mockImplementation(async (_token: string, appliesTo?: string) => {
      if (appliesTo === "document") {
        return [
          {
            id: 7,
            name: "Vertrag",
            applies_to: "document",
            attributes: [],
            icon: null,
            kennzeichen_display_override: false,
          },
        ];
      }
      return [];
    });

    renderWorkspace();

    expect(await screen.findByText(/Vertrag\.pdf/)).toBeInTheDocument();
    expect(screen.queryByText(/2026-001/)).not.toBeInTheDocument();
  });

  it("shows an assigned Kennzeichen read-only for users without the dms-admin role", async () => {
    const kennzeichenDoc = {
      ...document1,
      title: "Vertrag.pdf",
      attributes: { Kennzeichen: "2026-001" },
    };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([kennzeichenDoc]);

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Vertrag.pdf/));

    const metadataPanel = getPaneSectionByLabel("Metadaten");
    const kennzeichenInput = within(metadataPanel).getByLabelText("Kennzeichen");
    expect(kennzeichenInput).toHaveValue("2026-001");
    expect(kennzeichenInput).toBeDisabled();
    expect(
      within(metadataPanel).getByText(/Nur Nutzer mit der Rolle "dms-admin"/)
    ).toBeInTheDocument();
  });

  it("lets a dms-admin user change an assigned Kennzeichen", async () => {
    authState.realmRoles = ["dms-admin"];
    const kennzeichenDoc = {
      ...document1,
      title: "Vertrag.pdf",
      attributes: { Kennzeichen: "2026-001" },
    };
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([kennzeichenDoc]);
    updateDocumentMetadataMock.mockResolvedValue({
      ...kennzeichenDoc,
      attributes: { Kennzeichen: "2026-999" },
    });

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Vertrag.pdf/));

    const metadataPanel = getPaneSectionByLabel("Metadaten");
    // dockview verhält sich wie ein echter Editor: eine Panelgruppe muss
    // erst fokussiert/aktiviert werden ("Klick hinein"), bevor Elemente
    // darin interaktiv fokussierbar sind.
    await user.click(metadataPanel);
    const kennzeichenInput = within(metadataPanel).getByLabelText("Kennzeichen");
    expect(kennzeichenInput).not.toBeDisabled();
    await user.clear(kennzeichenInput);
    await user.type(kennzeichenInput, "2026-999");
    fireEvent.submit(within(metadataPanel).getByRole("form", { name: "Dokumentmetadaten bearbeiten" }));

    await waitFor(() =>
      expect(updateDocumentMetadataMock).toHaveBeenCalledWith("token-123", "d1", {
        title: "Vertrag.pdf",
        attributes: { Kennzeichen: "2026-999" },
      })
    );
  });

  describe("Sammelbearbeitung (multi-select + bulk edit, P14-S12)", () => {
    const doc2 = { ...document1, id: "d2", title: "Angebot.pdf" };

    it("shows the bulk-edit toolbar only once at least one item is selected, and lets it be cleared again", async () => {
      listChildFoldersMock.mockResolvedValue([
        { id: "f1", name: "Verträge", parent_id: "root", object_type_id: null, attributes: {} },
      ]);
      listDocumentsInFolderMock.mockResolvedValue([document1]);

      const user = userEvent.setup();
      renderWorkspace();

      await screen.findByText(/Rechnung.pdf/);
      expect(screen.queryByText("Sammelbearbeitung")).not.toBeInTheDocument();

      await user.click(screen.getByLabelText("Rechnung.pdf auswählen"));
      expect(await screen.findByText("1 ausgewählt")).toBeInTheDocument();
      expect(screen.getByText("Sammelbearbeitung")).toBeInTheDocument();

      await user.click(screen.getByLabelText("Verträge auswählen"));
      expect(await screen.findByText("2 ausgewählt")).toBeInTheDocument();

      await user.click(screen.getByText("Auswahl aufheben"));
      expect(screen.queryByText("Sammelbearbeitung")).not.toBeInTheDocument();
    });

    it("opens the bulk-edit modal with the selected documents and applies a shared field to all of them", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([
        { ...document1, object_type_id: 5, attributes: { Betrag: "100" } },
        { ...doc2, object_type_id: 5, attributes: { Betrag: "200" } },
      ]);
      getObjectTypeMock.mockResolvedValue({
        id: 5,
        name: "Vertrag",
        applies_to: "document",
        attributes: [{ name: "Betrag", type: "string" }],
        icon: null,
      });
      getObjectTypeLayoutMock.mockResolvedValue({
        rows: [{ columns: [{ attribute: "Betrag", label: "Betrag", required: false }] }],
        responsive_breakpoint_px: 600,
        is_custom: false,
      });
      updateDocumentMetadataMock.mockResolvedValue({});

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByLabelText("Rechnung.pdf auswählen"));
      await user.click(screen.getByLabelText("Angebot.pdf auswählen"));
      await user.click(screen.getByText("Sammelbearbeitung"));

      const modal = await screen.findByRole("dialog", { name: "Sammelbearbeitung (2 Objekte)" });
      const betragInput = await within(modal).findByLabelText("Betrag");
      await user.type(betragInput, "999");
      await user.click(within(modal).getByText("Übernehmen"));

      await waitFor(() => expect(updateDocumentMetadataMock).toHaveBeenCalledTimes(2));
      expect(updateDocumentMetadataMock).toHaveBeenCalledWith("token-123", "d1", {
        attributes: { Betrag: "999" },
      });
      expect(updateDocumentMetadataMock).toHaveBeenCalledWith("token-123", "d2", {
        attributes: { Betrag: "999" },
      });
      expect(within(modal).getAllByText("Erfolgreich")).toHaveLength(2);

      await user.click(within(modal).getByText("Schließen"));
      expect(screen.queryByText("Sammelbearbeitung")).not.toBeInTheDocument();
    });

    it("shows a blocking message for a heterogeneous document+folder selection instead of loading a form", async () => {
      listChildFoldersMock.mockResolvedValue([
        { id: "f1", name: "Verträge", parent_id: "root", object_type_id: null, attributes: {} },
      ]);
      listDocumentsInFolderMock.mockResolvedValue([document1]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByLabelText("Rechnung.pdf auswählen"));
      await user.click(screen.getByLabelText("Verträge auswählen"));
      await user.click(screen.getByText("Sammelbearbeitung"));

      expect(
        await screen.findByText(
          "Nur Dokumente ODER nur Ordner desselben Objekttyps können gemeinsam bearbeitet werden."
        )
      ).toBeInTheDocument();
      expect(getObjectTypeMock).not.toHaveBeenCalled();
    });

    it("clears the selection when navigating into a subfolder", async () => {
      listChildFoldersMock.mockImplementation(async (_token: string, folderId: string) => {
        if (folderId === "root") {
          return [
            { id: "f1", name: "Verträge", parent_id: "root", object_type_id: null, attributes: {} },
          ];
        }
        return [];
      });
      listDocumentsInFolderMock.mockResolvedValue([document1]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByLabelText("Rechnung.pdf auswählen"));
      expect(await screen.findByText("1 ausgewählt")).toBeInTheDocument();

      await user.click(screen.getByText(/Verträge/));

      await waitFor(() => expect(listChildFoldersMock).toHaveBeenCalledWith("token-123", "f1"));
      expect(screen.queryByText("Sammelbearbeitung")).not.toBeInTheDocument();
    });

    it("clears the selection when toggling the trash view", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([document1]);
      listDeletedDocumentsMock.mockResolvedValue([]);
      listDeletedFoldersMock.mockResolvedValue([]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(await screen.findByLabelText("Rechnung.pdf auswählen"));
      expect(await screen.findByText("1 ausgewählt")).toBeInTheDocument();

      await user.click(screen.getByText("Papierkorb anzeigen"));

      expect(screen.queryByText("Sammelbearbeitung")).not.toBeInTheDocument();
    });
  });

  describe("Papierkorb-Familie (persönlicher/Verschlusssachen-Papierkorb, P15-S1)", () => {
    it("shows only the personal trash view for a regular user, without a purge button", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([]);
      listDeletedDocumentsGlobalMock.mockResolvedValue([{ ...document1, deleted_at: "2026-01-05T00:00:00Z" }]);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(screen.getByTitle("Papierkorb"));

      expect(await screen.findByText(/Rechnung.pdf/)).toBeInTheDocument();
      expect(listDeletedDocumentsGlobalMock).toHaveBeenCalledWith("token-123", "personal");
      expect(screen.queryByText("Vollständiger Papierkorb")).not.toBeInTheDocument();
      expect(screen.queryByText("Endgültig löschen")).not.toBeInTheDocument();
    });

    it("offers the admin trash scope only for a dms-admin user and purges a document", async () => {
      authState.realmRoles = ["dms-admin"];
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([]);
      listDeletedDocumentsGlobalMock.mockResolvedValue([{ ...document1, deleted_at: "2026-01-05T00:00:00Z" }]);
      vi.spyOn(window, "confirm").mockReturnValue(true);

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(screen.getByTitle("Papierkorb"));
      await user.click(await screen.findByText("Vollständiger Papierkorb"));

      await waitFor(() => expect(listDeletedDocumentsGlobalMock).toHaveBeenCalledWith("token-123", "admin"));
      await user.click(await screen.findByText("Endgültig löschen"));

      expect(purgeDocumentMock).toHaveBeenCalledWith("token-123", "d1");
    });

    it("restores a deleted folder from the trash pane", async () => {
      listChildFoldersMock.mockResolvedValue([]);
      listDocumentsInFolderMock.mockResolvedValue([]);
      const deletedFolder: Folder = {
        id: "f-trash",
        name: "Alte Akte",
        parent_id: "root",
        object_type_id: null,
        attributes: {},
        deleted_at: "2026-01-05T00:00:00Z",
        deleted_by: "alice",
        retention_until: null,
        full_deletion: false,
        pending_deletion_reason: null,
      };
      listDeletedFoldersGlobalMock.mockResolvedValue([deletedFolder]);
      restoreFolderMock.mockResolvedValue({ ...deletedFolder, deleted_at: null });

      const user = userEvent.setup();
      renderWorkspace();

      await user.click(screen.getByTitle("Papierkorb"));
      await user.click(await screen.findByText("Wiederherstellen"));

      expect(restoreFolderMock).toHaveBeenCalledWith("token-123", "f-trash");
    });
  });
});
