import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskPane } from "@/components/TaskPane";
import { AuthProvider } from "@/lib/auth-context";
import { I18nProvider } from "@/i18n";
import { installOfficeMock, type OfficeMockState } from "./office-mock";

const searchDocumentsMock = vi.fn();
const getDocumentMock = vi.fn();
const downloadDocumentContentMock = vi.fn();
const acquireLockMock = vi.fn();
const releaseLockMock = vi.fn();
const checkinVersionMock = vi.fn();
const createDocumentMock = vi.fn();
const updateDocumentMetadataMock = vi.fn();
const listRootFoldersMock = vi.fn();
const listDocumentsInFolderMock = vi.fn();
const getObjectTypeMock = vi.fn();
const listInstancesForDocumentMock = vi.fn();
const listInstanceTasksMock = vi.fn();
const listProcessDefinitionsMock = vi.fn();

// AuthProvider needs real `login`/`getCurrentUser` - independent of
// the rest of the `@/lib/api` mock, since AuthProvider internally
// imports `getCurrentUser`. Instead of mocking AuthProvider itself,
// an already-logged-in state is simulated via a pre-set localStorage
// entry (identical pattern to the `restores a still-valid session` test in
// auth-context.test.tsx), plus a mocked `getCurrentUser` return value.
const getCurrentUserMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    searchDocuments: (...args: unknown[]) => searchDocumentsMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
    downloadDocumentContent: (...args: unknown[]) => downloadDocumentContentMock(...args),
    acquireLock: (...args: unknown[]) => acquireLockMock(...args),
    releaseLock: (...args: unknown[]) => releaseLockMock(...args),
    checkinVersion: (...args: unknown[]) => checkinVersionMock(...args),
    createDocument: (...args: unknown[]) => createDocumentMock(...args),
    updateDocumentMetadata: (...args: unknown[]) => updateDocumentMetadataMock(...args),
    listRootFolders: (...args: unknown[]) => listRootFoldersMock(...args),
    listDocumentsInFolder: (...args: unknown[]) => listDocumentsInFolderMock(...args),
    getObjectType: (...args: unknown[]) => getObjectTypeMock(...args),
    listInstancesForDocument: (...args: unknown[]) => listInstancesForDocumentMock(...args),
    listInstanceTasks: (...args: unknown[]) => listInstanceTasksMock(...args),
    listProcessDefinitions: (...args: unknown[]) => listProcessDefinitionsMock(...args),
    getCurrentUser: (...args: unknown[]) => getCurrentUserMock(...args),
  };
});

const DOC = {
  id: "doc-1",
  title: "Vertragsentwurf",
  folder_id: null,
  object_type_id: null,
  attributes: {},
  current_version_number: 2,
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderTaskPane() {
  return render(
    <I18nProvider>
      <AuthProvider>
        <TaskPane />
      </AuthProvider>
    </I18nProvider>
  );
}

describe("TaskPane", () => {
  let officeState: OfficeMockState;

  beforeEach(() => {
    officeState = installOfficeMock();
    window.localStorage.clear();
    window.localStorage.setItem(
      "ogdoc.tokens",
      JSON.stringify({
        accessToken: "token-123",
        refreshToken: "refresh-123",
        expiresAt: Date.now() + 60_000,
      })
    );
    getCurrentUserMock.mockReset().mockResolvedValue({
      sub: "alice-sub",
      username: "alice",
      email: null,
      realm_roles: [],
    });
    for (const mock of [
      searchDocumentsMock,
      getDocumentMock,
      downloadDocumentContentMock,
      acquireLockMock,
      releaseLockMock,
      checkinVersionMock,
      createDocumentMock,
      updateDocumentMetadataMock,
      listRootFoldersMock,
      listDocumentsInFolderMock,
      getObjectTypeMock,
      listInstancesForDocumentMock,
      listInstanceTasksMock,
      listProcessDefinitionsMock,
    ]) {
      mock.mockReset();
    }
    listRootFoldersMock.mockResolvedValue([]);
    listInstancesForDocumentMock.mockResolvedValue([]);
    listProcessDefinitionsMock.mockResolvedValue([]);
    downloadDocumentContentMock.mockResolvedValue(new Blob(["docx-bytes"]));
  });

  it("shows the document- and template-picker when nothing is linked", async () => {
    renderTaskPane();
    expect(await screen.findByText("Aus OG Doc öffnen")).toBeInTheDocument();
    expect(await screen.findByText("Neu aus Vorlage")).toBeInTheDocument();
  });

  it("opens an existing document: loads it into Word and links it", async () => {
    searchDocumentsMock.mockResolvedValue([{ id: "doc-1", title: "Vertragsentwurf", folder_name: null }]);
    getDocumentMock.mockResolvedValue(DOC);
    acquireLockMock.mockResolvedValue({
      document_id: "doc-1",
      locked_by: "alice",
      session_id: "s1",
      based_on_version_number: 2,
      expires_at: "2026-01-01T01:00:00Z",
    });

    const user = userEvent.setup();
    renderTaskPane();

    await user.type(await screen.findByPlaceholderText("Dokument suchen..."), "Vertrag");
    await user.click(screen.getByText("Suchen"));
    await user.click(await screen.findByText("Öffnen"));

    await waitFor(() => expect(acquireLockMock).toHaveBeenCalled());
    expect(officeState.lastInsertedBase64).not.toBeNull();
    expect(await screen.findByText("Vertragsentwurf")).toBeInTheDocument();
    expect(await screen.findByText("In OG Doc speichern")).toBeInTheDocument();
    expect(screen.getByText("Workflow")).toBeInTheDocument();
  });

  it("shows a read-only hint when the document is locked by someone else", async () => {
    searchDocumentsMock.mockResolvedValue([{ id: "doc-1", title: "Vertragsentwurf", folder_name: null }]);
    getDocumentMock.mockResolvedValue(DOC);
    class ApiErr extends Error {
      status = 409;
    }
    acquireLockMock.mockRejectedValue(new ApiErr("Dokument doc-1 ist gesperrt von bob"));

    const user = userEvent.setup();
    renderTaskPane();

    await user.type(await screen.findByPlaceholderText("Dokument suchen..."), "Vertrag");
    await user.click(screen.getByText("Suchen"));
    await user.click(await screen.findByText("Öffnen"));

    expect(await screen.findByText(/nur lesend geöffnet/)).toBeInTheDocument();
    const saveButton = screen.getByText("In OG Doc speichern") as HTMLButtonElement;
    expect(saveButton).toBeDisabled();
  });

  it("creates a new document from a template on first save", async () => {
    listRootFoldersMock.mockResolvedValue([{ id: "folder-vorlagen", name: "Vorlagen", parent_id: "root" }]);
    listDocumentsInFolderMock.mockResolvedValue([
      { ...DOC, id: "tpl-1", title: "Standardvertrag" },
    ]);
    createDocumentMock.mockResolvedValue({ ...DOC, id: "doc-new", current_version_number: 1 });
    acquireLockMock.mockResolvedValue({
      document_id: "doc-new",
      locked_by: "alice",
      session_id: "s1",
      based_on_version_number: 1,
      expires_at: "2026-01-01T01:00:00Z",
    });

    const user = userEvent.setup();
    renderTaskPane();

    await user.click(await screen.findByText("Verwenden"));
    expect(await screen.findByText("Neues Dokument aus Vorlage")).toBeInTheDocument();

    await user.click(screen.getByText("Als neues Dokument in OG Doc speichern"));

    await waitFor(() => expect(createDocumentMock).toHaveBeenCalled());
    expect(createDocumentMock.mock.calls[0][1]).toMatchObject({
      derivedFromDocumentId: "tpl-1",
      derivedFromVersionNumber: 2,
    });
  });

  it("saves a new version and advances the linked version number", async () => {
    searchDocumentsMock.mockResolvedValue([{ id: "doc-1", title: "Vertragsentwurf", folder_name: null }]);
    getDocumentMock.mockResolvedValue(DOC);
    acquireLockMock.mockResolvedValue({
      document_id: "doc-1",
      locked_by: "alice",
      session_id: "s1",
      based_on_version_number: 2,
      expires_at: "2026-01-01T01:00:00Z",
    });
    checkinVersionMock.mockResolvedValue({ version: { version_number: 3 }, is_conflict: false });

    const user = userEvent.setup();
    renderTaskPane();

    await user.type(await screen.findByPlaceholderText("Dokument suchen..."), "Vertrag");
    await user.click(screen.getByText("Suchen"));
    await user.click(await screen.findByText("Öffnen"));
    await screen.findByText("In OG Doc speichern");

    await user.click(screen.getByText("In OG Doc speichern"));

    await waitFor(() =>
      expect(checkinVersionMock).toHaveBeenCalledWith(
        "token-123",
        "doc-1",
        expect.objectContaining({ expectedBaseVersionNumber: 2 })
      )
    );
    expect(await screen.findByText("In OG Doc gespeichert.")).toBeInTheDocument();
  });

  it("unlinking releases the lock and returns to the picker view", async () => {
    searchDocumentsMock.mockResolvedValue([{ id: "doc-1", title: "Vertragsentwurf", folder_name: null }]);
    getDocumentMock.mockResolvedValue(DOC);
    acquireLockMock.mockResolvedValue({
      document_id: "doc-1",
      locked_by: "alice",
      session_id: "s1",
      based_on_version_number: 2,
      expires_at: "2026-01-01T01:00:00Z",
    });

    const user = userEvent.setup();
    renderTaskPane();

    await user.type(await screen.findByPlaceholderText("Dokument suchen..."), "Vertrag");
    await user.click(screen.getByText("Suchen"));
    await user.click(await screen.findByText("Öffnen"));
    await screen.findByText("Verknüpfung lösen");

    await user.click(screen.getByText("Verknüpfung lösen"));

    await waitFor(() => expect(releaseLockMock).toHaveBeenCalledWith("token-123", "doc-1", "alice"));
    expect(await screen.findByText("Aus OG Doc öffnen")).toBeInTheDocument();
  });
});
