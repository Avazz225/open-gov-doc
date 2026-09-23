import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CasesPane } from "@/components/CasesPane";
import { I18nProvider } from "@/i18n";

const listCasesMock = vi.fn();
const getCaseMock = vi.fn();
const listCaseDocumentsMock = vi.fn();
const getCaseDocumentMock = vi.fn();
const downloadDocumentVersionMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listCases: (...args: unknown[]) => listCasesMock(...args),
  getCase: (...args: unknown[]) => getCaseMock(...args),
  listCaseDocuments: (...args: unknown[]) => listCaseDocumentsMock(...args),
  getCaseDocument: (...args: unknown[]) => getCaseDocumentMock(...args),
  downloadDocumentVersion: (...args: unknown[]) => downloadDocumentVersionMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
    permissions: [],
    accessToken: "token-123",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

function renderPane() {
  return render(
    <I18nProvider>
      <CasesPane />
    </I18nProvider>
  );
}

const CASE_A = {
  id: "case-1",
  name: "Beispielvorgang",
  status: "open",
  created_at: "2026-01-01T10:00:00Z",
  closed_at: null,
  vorgangsnummer: "VG-1",
};

describe("CasesPane", () => {
  beforeEach(() => {
    listCasesMock.mockReset();
    getCaseMock.mockReset();
    listCaseDocumentsMock.mockReset();
    getCaseDocumentMock.mockReset();
    downloadDocumentVersionMock.mockReset();
  });

  it("shows the empty state when no cases exist", async () => {
    listCasesMock.mockResolvedValue([]);
    renderPane();
    await waitFor(() => expect(screen.getByText("Keine Vorgänge vorhanden.")).toBeTruthy());
  });

  it("lists cases and opens the detail view on click", async () => {
    listCasesMock.mockResolvedValue([CASE_A]);
    getCaseMock.mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockResolvedValue([]);
    renderPane();

    const row = await screen.findByText("Beispielvorgang (VG-1)");
    fireEvent.click(row);

    await waitFor(() => expect(getCaseMock).toHaveBeenCalledWith("token-123", "case-1"));
    expect(await screen.findByText("Dieser Vorgang enthält noch keine Dokumente.")).toBeTruthy();
  });

  it("navigates back to the list", async () => {
    listCasesMock.mockResolvedValue([CASE_A]);
    getCaseMock.mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockResolvedValue([]);
    renderPane();

    fireEvent.click(await screen.findByText("Beispielvorgang (VG-1)"));
    await screen.findByText("Dieser Vorgang enthält noch keine Dokumente.");
    fireEvent.click(screen.getByText("Zurück zur Übersicht"));

    await waitFor(() => expect(screen.getByText("Beispielvorgang (VG-1)")).toBeTruthy());
  });

  it("shows a non-clickable placeholder for a deleted document reference", async () => {
    listCasesMock.mockResolvedValue([CASE_A]);
    getCaseMock.mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockResolvedValue([
      {
        document_id: "doc-gone",
        added_at: "2026-01-02T10:00:00Z",
        document_deleted_at: "2026-01-03T10:00:00Z",
      },
    ]);
    renderPane();

    fireEvent.click(await screen.findByText("Beispielvorgang (VG-1)"));
    expect(await screen.findByText("Dokument gelöscht")).toBeTruthy();
    expect(getCaseDocumentMock).not.toHaveBeenCalled();
  });

  it("resolves a document's title and downloads it on click", async () => {
    listCasesMock.mockResolvedValue([CASE_A]);
    getCaseMock.mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockResolvedValue([
      { document_id: "doc-1", added_at: "2026-01-02T10:00:00Z", document_deleted_at: null },
    ]);
    getCaseDocumentMock.mockResolvedValue({
      id: "doc-1",
      title: "Anschreiben.pdf",
      current_version_number: 2,
    });
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["x"]));
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    // jsdom has no real Blob URL support.
    (URL as unknown as { createObjectURL: typeof createObjectURL }).createObjectURL =
      createObjectURL;
    (URL as unknown as { revokeObjectURL: typeof revokeObjectURL }).revokeObjectURL =
      revokeObjectURL;

    renderPane();
    fireEvent.click(await screen.findByText("Beispielvorgang (VG-1)"));
    const docButton = await screen.findByText("Anschreiben.pdf");
    fireEvent.click(docButton);

    await waitFor(() =>
      expect(downloadDocumentVersionMock).toHaveBeenCalledWith("token-123", "doc-1", 2)
    );
  });
});
