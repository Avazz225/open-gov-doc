import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FolderBrowser } from "@/components/FolderBrowser";
import { I18nProvider } from "@/i18n";

function renderFolderBrowser() {
  return render(
    <I18nProvider>
      <FolderBrowser />
    </I18nProvider>
  );
}

const listChildFoldersMock = vi.fn();
const listDocumentsInFolderMock = vi.fn();
const downloadDocumentMock = vi.fn();
const uploadDocumentMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listChildFolders: (...args: unknown[]) => listChildFoldersMock(...args),
  listDocumentsInFolder: (...args: unknown[]) => listDocumentsInFolderMock(...args),
  downloadDocument: (...args: unknown[]) => downloadDocumentMock(...args),
  uploadDocument: (...args: unknown[]) => uploadDocumentMock(...args),
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

describe("FolderBrowser", () => {
  beforeEach(() => {
    listChildFoldersMock.mockReset();
    listDocumentsInFolderMock.mockReset();
    downloadDocumentMock.mockReset();
    uploadDocumentMock.mockReset();
  });

  it("lists subfolders and documents of the current folder", async () => {
    listChildFoldersMock.mockResolvedValue([
      { id: "f1", name: "Verträge", parent_id: "root", object_type_id: null, attributes: {} },
    ]);
    listDocumentsInFolderMock.mockResolvedValue([
      {
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
      },
    ]);

    renderFolderBrowser();

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
    renderFolderBrowser();

    const folderButton = await screen.findByText(/Verträge/);
    await user.click(folderButton);

    await waitFor(() =>
      expect(listChildFoldersMock).toHaveBeenCalledWith("token-123", "f1")
    );
    const breadcrumbs = screen.getByLabelText("Ordnerpfad");
    expect(within(breadcrumbs).getByText("Verträge")).toBeInTheDocument();
    expect(within(breadcrumbs).getByText("Start")).toBeInTheDocument();
  });

  it("shows the preview stub instead of a real preview", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([
      {
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
      },
    ]);

    const user = userEvent.setup();
    renderFolderBrowser();

    await user.click(await screen.findByText("Vorschau"));

    expect(
      screen.getByText(/Vorschau ist noch nicht verfügbar/)
    ).toBeInTheDocument();
  });

  it("reloads the document list after a successful upload", async () => {
    listChildFoldersMock.mockResolvedValue([]);
    listDocumentsInFolderMock.mockResolvedValue([]);
    uploadDocumentMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderFolderBrowser();

    await waitFor(() => expect(listDocumentsInFolderMock).toHaveBeenCalledTimes(1));

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const fileInput = screen.getByLabelText("Datei");
    await user.upload(fileInput, file);
    // fireEvent.submit statt Klick auf den Submit-Button: umgeht jsdoms native
    // Constraint-Validation für `required`-Datei-Inputs, die den Submit sonst
    // unzuverlässig unterdrückt, ohne dass das eigentliche Formularverhalten
    // (onSubmit-Handler) geändert wird.
    fireEvent.submit(screen.getByRole("form", { name: "Dokument hochladen" }));

    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalled());
    await waitFor(() => expect(listDocumentsInFolderMock).toHaveBeenCalledTimes(2));
  });
});
