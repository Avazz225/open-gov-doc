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

vi.mock("@/lib/api", () => ({
  listChildFolders: (...args: unknown[]) => listChildFoldersMock(...args),
  listDocumentsInFolder: (...args: unknown[]) => listDocumentsInFolderMock(...args),
  downloadDocument: (...args: unknown[]) => downloadDocumentMock(...args),
  uploadDocument: (...args: unknown[]) => uploadDocumentMock(...args),
  createFolder: (...args: unknown[]) => createFolderMock(...args),
  renameFolder: (...args: unknown[]) => renameFolderMock(...args),
  deleteFolder: (...args: unknown[]) => deleteFolderMock(...args),
  getObjectType: (...args: unknown[]) => getObjectTypeMock(...args),
  updateDocumentMetadata: (...args: unknown[]) => updateDocumentMetadataMock(...args),
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

    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText(/Rechnung.pdf/));

    const tabBar = screen.getByRole("tablist");
    expect(within(tabBar).getByText("Rechnung.pdf")).toBeInTheDocument();
    const previewPane = screen.getByLabelText("Vorschau");
    expect(within(previewPane).getByText("Rechnung.pdf")).toBeInTheDocument();
    expect(screen.getByText(/Vorschau ist noch nicht verfügbar/)).toBeInTheDocument();
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
});
