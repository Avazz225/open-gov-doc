import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CasesPane } from "@/components/CasesPane";
import { I18nProvider } from "@/i18n";
import type { Case, CaseDocumentReference } from "@/lib/api";

const listCasesMock = vi.fn();
const getCaseMock = vi.fn();
const listCaseDocumentsMock = vi.fn();
const getDocumentMock = vi.fn();
const exportCaseXdomeaMock = vi.fn();
const exportCaseXjustizMock = vi.fn();
const importXdomeaIntoCaseMock = vi.fn();
const importXjustizIntoCaseMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listCases: (...args: unknown[]) => listCasesMock(...args),
    getCase: (...args: unknown[]) => getCaseMock(...args),
    listCaseDocuments: (...args: unknown[]) => listCaseDocumentsMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
    exportCaseXdomea: (...args: unknown[]) => exportCaseXdomeaMock(...args),
    exportCaseXjustiz: (...args: unknown[]) => exportCaseXjustizMock(...args),
    importXdomeaIntoCase: (...args: unknown[]) => importXdomeaIntoCaseMock(...args),
    importXjustizIntoCase: (...args: unknown[]) => importXjustizIntoCaseMock(...args),
  };
});

let mockPermissions: string[] = ["archival.write"];
vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
      permissions: mockPermissions,
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const CASE_A: Case = {
  id: "case-1",
  name: "Umlaufmappe A",
  object_type_id: null,
  attributes: {},
  status: "open",
  process_definition_id: 42,
  process_instance_id: "instance-1",
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  closed_at: null,
  archive_after: null,
  archived_at: null,
  vorgangsnummer: "2026-001",
  registered_at: "2026-01-01T00:00:00Z",
};

const DOCUMENT_REF: CaseDocumentReference = {
  document_id: "doc-1",
  added_by: "alice",
  added_at: "2026-01-02T00:00:00Z",
  removed_by: null,
  removed_at: null,
  snapshot_version_number: null,
  current_version_number: 1,
  document_deleted_at: null,
  has_active_quarantine: false,
};

function renderPane(onOpenDocument = vi.fn()) {
  return render(
    <I18nProvider>
      <CasesPane token="token-123" onOpenDocument={onOpenDocument} />
    </I18nProvider>
  );
}

describe("CasesPane", () => {
  beforeEach(() => {
    listCasesMock.mockReset().mockResolvedValue([CASE_A]);
    getCaseMock.mockReset().mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockReset().mockResolvedValue([DOCUMENT_REF]);
    getDocumentMock.mockReset();
    exportCaseXdomeaMock.mockReset();
    exportCaseXjustizMock.mockReset();
    importXdomeaIntoCaseMock.mockReset();
    importXjustizIntoCaseMock.mockReset();
    mockPermissions = ["archival.write"];
  });

  it("shows the empty state when no cases exist", async () => {
    listCasesMock.mockResolvedValue([]);
    renderPane();

    expect(await screen.findByText("Keine Umlaufmappen vorhanden.")).toBeInTheDocument();
  });

  it("lists cases and opens the detail view on click", async () => {
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));

    await waitFor(() => expect(getCaseMock).toHaveBeenCalledWith("token-123", "case-1"));
    expect(await screen.findByRole("heading", { name: "Umlaufmappe A" })).toBeInTheDocument();
    expect(screen.getByText("doc-1")).toBeInTheDocument();
    expect(screen.getByText("Zurück zur Übersicht")).toBeInTheDocument();
  });

  it("returns to the list via the back button", async () => {
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await screen.findByRole("heading", { name: "Umlaufmappe A" });

    await user.click(screen.getByText("Zurück zur Übersicht"));

    expect(await screen.findByText("Umlaufmappe A (2026-001)")).toBeInTheDocument();
  });

  it("shows a placeholder instead of a link for a deleted document reference", async () => {
    listCaseDocumentsMock.mockResolvedValue([
      { ...DOCUMENT_REF, document_deleted_at: "2026-02-01T00:00:00Z" },
    ]);
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));

    expect(await screen.findByText("Dokument gelöscht")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "doc-1" })).not.toBeInTheDocument();
  });

  it("shows a quarantine indicator for a document reference under active records quarantine", async () => {
    listCaseDocumentsMock.mockResolvedValue([{ ...DOCUMENT_REF, has_active_quarantine: true }]);
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));

    expect(await screen.findByText(/unter Schriftgutquarantäne/)).toBeInTheDocument();
  });

  it("opens a case document via onOpenDocument", async () => {
    const onOpenDocument = vi.fn();
    getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Rechnung" });
    const user = userEvent.setup();
    renderPane(onOpenDocument);

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await user.click(await screen.findByText("doc-1"));

    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledWith("token-123", "doc-1"));
    expect(onOpenDocument).toHaveBeenCalledWith({ id: "doc-1", title: "Rechnung" });
  });

  it("hides export/import actions without admin.deletion_classified-style archival.write", async () => {
    mockPermissions = [];
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await screen.findByRole("heading", { name: "Umlaufmappe A" });

    expect(screen.queryByText("Export für Behördenübergabe")).not.toBeInTheDocument();
    expect(screen.queryByText("Export für Justizübergabe")).not.toBeInTheDocument();
    expect(screen.queryByText("XDOMEA-Import")).not.toBeInTheDocument();
    expect(screen.queryByText("XJustiz-Import")).not.toBeInTheDocument();
  });

  it("exports the case via XDOMEA with the entered recipient", async () => {
    exportCaseXdomeaMock.mockResolvedValue(new Blob(["PK-zip"], { type: "application/zip" }));
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await user.click(await screen.findByText("Export für Behördenübergabe"));
    await user.type(screen.getByLabelText("Empfangende Behörde"), "Landesarchiv Test");
    await user.click(screen.getByText("Paket exportieren"));

    expect(exportCaseXdomeaMock).toHaveBeenCalledWith("token-123", "case-1", "Landesarchiv Test");
  });

  it("exports the case via XJustiz with the entered recipient", async () => {
    exportCaseXjustizMock.mockResolvedValue(new Blob(["PK-zip"], { type: "application/zip" }));
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await user.click(await screen.findByText("Export für Justizübergabe"));
    await user.type(screen.getByLabelText("Empfangende Justizbehörde"), "Testgericht");
    await user.click(screen.getByText("Justiz-Paket exportieren"));

    expect(exportCaseXjustizMock).toHaveBeenCalledWith("token-123", "case-1", "Testgericht");
  });

  it("imports an XDOMEA package into this case", async () => {
    importXdomeaIntoCaseMock.mockResolvedValue({
      case_id: "case-1",
      case_created: false,
      vorgang_betreff: "Umlaufmappe A",
      document_ids: ["new-doc-1"],
    });
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await user.click(await screen.findByText("XDOMEA-Import"));

    const form = screen.getByText("Paket-Datei (ZIP)").closest(".inline-form") as HTMLElement;
    const file = new File(["PK-zip"], "abgabe.zip", { type: "application/zip" });
    await user.upload(within(form).getByLabelText("Paket-Datei (ZIP)"), file);
    await user.click(within(form).getByText("Paket importieren"));

    await waitFor(() =>
      expect(importXdomeaIntoCaseMock).toHaveBeenCalledWith("token-123", {
        file,
        folderId: "root",
        caseId: "case-1",
      })
    );
    expect(await screen.findByText("1 Dokument(e) importiert.")).toBeInTheDocument();
  });

  it("imports an XJustiz package into this case", async () => {
    importXjustizIntoCaseMock.mockResolvedValue({
      case_id: "case-1",
      case_created: false,
      akte_anzeigename: "Umlaufmappe A",
      document_ids: ["new-doc-2"],
    });
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await user.click(await screen.findByText("XJustiz-Import"));

    const form = screen.getByText("Paket-Datei (ZIP)").closest(".inline-form") as HTMLElement;
    const file = new File(["PK-zip"], "xjustiz.zip", { type: "application/zip" });
    await user.upload(within(form).getByLabelText("Paket-Datei (ZIP)"), file);
    await user.click(within(form).getByText("Paket importieren"));

    await waitFor(() =>
      expect(importXjustizIntoCaseMock).toHaveBeenCalledWith("token-123", {
        file,
        folderId: "root",
        caseId: "case-1",
      })
    );
    expect(await screen.findByText("1 Dokument(e) importiert.")).toBeInTheDocument();
  });
});
