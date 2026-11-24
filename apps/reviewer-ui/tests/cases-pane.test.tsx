import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CasesPane } from "@/components/CasesPane";
import { I18nProvider } from "@/i18n";

const listCasesMock = vi.fn();
const getCaseMock = vi.fn();
const listCaseDocumentsMock = vi.fn();
const getCaseDocumentMock = vi.fn();
const downloadDocumentVersionMock = vi.fn();
const listCaseRemindersMock = vi.fn();
const createCaseReminderMock = vi.fn();
const completeReminderMock = vi.fn();
const getIncomingSignatureSummariesMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listCases: (...args: unknown[]) => listCasesMock(...args),
  getCase: (...args: unknown[]) => getCaseMock(...args),
  listCaseDocuments: (...args: unknown[]) => listCaseDocumentsMock(...args),
  getCaseDocument: (...args: unknown[]) => getCaseDocumentMock(...args),
  downloadDocumentVersion: (...args: unknown[]) => downloadDocumentVersionMock(...args),
  listCaseReminders: (...args: unknown[]) => listCaseRemindersMock(...args),
  createCaseReminder: (...args: unknown[]) => createCaseReminderMock(...args),
  completeReminder: (...args: unknown[]) => completeReminderMock(...args),
  getIncomingSignatureSummaries: (...args: unknown[]) => getIncomingSignatureSummariesMock(...args),
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
    getIncomingSignatureSummariesMock.mockReset().mockResolvedValue({});
    listCasesMock.mockReset();
    getCaseMock.mockReset();
    listCaseDocumentsMock.mockReset();
    getCaseDocumentMock.mockReset();
    downloadDocumentVersionMock.mockReset();
    listCaseRemindersMock.mockReset().mockResolvedValue([]);
    createCaseReminderMock.mockReset().mockResolvedValue({});
    completeReminderMock.mockReset().mockResolvedValue(undefined);
  });

  it("sets and completes the reviewer's own follow-up on a case (P90-S2)", async () => {
    listCasesMock.mockResolvedValue([CASE_A]);
    getCaseMock.mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockResolvedValue([]);
    renderPane();
    fireEvent.click(await screen.findByText("Beispielvorgang (VG-1)"));
    expect(await screen.findByText("Keine offene Wiedervorlage für diesen Vorgang.")).toBeTruthy();
    expect(listCaseRemindersMock).toHaveBeenCalledWith("token-123", "alice", "case-1");

    fireEvent.change(screen.getByLabelText("Datum"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("Uhrzeit"), { target: { value: "10:30" } });
    fireEvent.change(screen.getByLabelText("Notiz"), { target: { value: "Gutachten nachfordern" } });
    listCaseRemindersMock.mockResolvedValue([
      {
        id: "r-1",
        user_id: "alice",
        object_type: "case",
        object_id: "case-1",
        due_at: new Date(2026, 11, 1, 10, 30).toISOString(),
        note: "Gutachten nachfordern",
        status: "open",
        is_due: false,
      },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Wiedervorlage setzen" }));

    await waitFor(() =>
      expect(createCaseReminderMock).toHaveBeenCalledWith("token-123", {
        user_id: "alice",
        case_id: "case-1",
        due_at: new Date(2026, 11, 1, 10, 30).toISOString(),
        note: "Gutachten nachfordern",
      })
    );
    expect(await screen.findByText("Gutachten nachfordern")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Erledigt" }));
    await waitFor(() => expect(completeReminderMock).toHaveBeenCalledWith("token-123", "r-1"));
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

  it("shows the signature badge of a document's current version (P92-S3)", async () => {
    listCasesMock.mockResolvedValue([CASE_A]);
    getCaseMock.mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockResolvedValue([
      { document_id: "doc-1", added_at: "2026-01-02T10:00:00Z", document_deleted_at: null },
      { document_id: "doc-2", added_at: "2026-01-02T10:00:00Z", document_deleted_at: null },
    ]);
    getCaseDocumentMock.mockImplementation((_token: string, id: string) =>
      Promise.resolve({ id, title: `${id}.pdf`, current_version_number: 2 })
    );
    const summary = {
      run_id: "r",
      signature_count: 1,
      best_qualification: "ades",
      changed_after_signing: false,
      truncated: false,
      validated_at: "2026-11-24T10:00:00Z",
    };
    getIncomingSignatureSummariesMock.mockResolvedValue({
      // doc-1: the current version (2) failed; an older valid one doesn't count.
      "doc-1": [
        { ...summary, version_number: 1, worst_indication: "TOTAL-PASSED" },
        { ...summary, version_number: 2, worst_indication: "TOTAL-FAILED" },
      ],
      // doc-2: only an older version was signed - no badge.
      "doc-2": [{ ...summary, version_number: 1, worst_indication: "TOTAL-PASSED" }],
    });

    renderPane();
    fireEvent.click(await screen.findByText("Beispielvorgang (VG-1)"));

    expect(await screen.findByText("Signatur ungültig")).toBeTruthy();
    expect(screen.getAllByTestId("signature-badge")).toHaveLength(1);
    expect(getIncomingSignatureSummariesMock).toHaveBeenCalledWith(
      "token-123",
      expect.arrayContaining(["doc-1", "doc-2"])
    );
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
