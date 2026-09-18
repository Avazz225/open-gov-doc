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
const listProcessDefinitionsMock = vi.fn();
const listFavoritesMock = vi.fn();
const addFavoriteMock = vi.fn();
const removeFavoriteMock = vi.fn();

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
    listProcessDefinitions: (...args: unknown[]) => listProcessDefinitionsMock(...args),
    listFavorites: (...args: unknown[]) => listFavoritesMock(...args),
    addFavorite: (...args: unknown[]) => addFavoriteMock(...args),
    removeFavorite: (...args: unknown[]) => removeFavoriteMock(...args),
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

function renderPane(onOpenDocument = vi.fn(), openCaseId: string | null = null) {
  return render(
    <I18nProvider>
      <CasesPane
        token="token-123"
        onOpenDocument={onOpenDocument}
        openCaseId={openCaseId}
      />
    </I18nProvider>
  );
}

describe("CasesPane", () => {
  beforeEach(() => {
    listCasesMock.mockReset().mockResolvedValue([CASE_A]);
    getCaseMock.mockReset().mockResolvedValue(CASE_A);
    listCaseDocumentsMock.mockReset().mockResolvedValue([DOCUMENT_REF]);
    getDocumentMock.mockReset().mockResolvedValue({ id: "doc-1", title: "Rechnung" });
    exportCaseXdomeaMock.mockReset();
    exportCaseXjustizMock.mockReset();
    importXdomeaIntoCaseMock.mockReset();
    importXjustizIntoCaseMock.mockReset();
    listProcessDefinitionsMock.mockReset().mockResolvedValue([]);
    listFavoritesMock.mockReset().mockResolvedValue([]);
    addFavoriteMock.mockReset().mockResolvedValue(undefined);
    removeFavoriteMock.mockReset().mockResolvedValue(undefined);
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
    // Phase 45 Session 5 - the row shows the resolved document title, not
    // the raw document_id, once `getDocument` resolves.
    expect(await screen.findByText("Rechnung")).toBeInTheDocument();
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
    // Phase 45 Session 5 - no title resolution attempted for an already
    // known-deleted reference (document_deleted_at already covers it).
    expect(getDocumentMock).not.toHaveBeenCalled();
  });

  it("shows a placeholder instead of a link when title resolution fails for a non-deleted reference", async () => {
    getDocumentMock.mockRejectedValue(new Error("404"));
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));

    expect(await screen.findByText("Dokumenttitel nicht verfügbar")).toBeInTheDocument();
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
    const user = userEvent.setup();
    renderPane(onOpenDocument);

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await user.click(await screen.findByText("Rechnung"));

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

  it("does not show the new-case import section when no process definitions exist", async () => {
    renderPane();

    await screen.findByText("Umlaufmappe A (2026-001)");
    expect(screen.queryByText("Neue Umlaufmappe per Import anlegen")).not.toBeInTheDocument();
  });

  it("hides the new-case import section without archival.write", async () => {
    mockPermissions = [];
    listProcessDefinitionsMock.mockResolvedValue([{ id: 1, name: "Standardprozess", version: 1 }]);
    renderPane();

    await screen.findByText("Umlaufmappe A (2026-001)");
    expect(screen.queryByText("Neue Umlaufmappe per Import anlegen")).not.toBeInTheDocument();
  });

  it("creates a new case via XDOMEA import with the selected process definition and navigates into it", async () => {
    listProcessDefinitionsMock.mockResolvedValue([{ id: 7, name: "Standardprozess", version: 1 }]);
    importXdomeaIntoCaseMock.mockResolvedValue({
      case_id: "case-new-1",
      case_created: true,
      vorgang_betreff: "Neuer Vorgang",
      document_ids: ["new-doc-3"],
    });
    getCaseMock.mockImplementation((_token: string, caseId: string) =>
      Promise.resolve({ ...CASE_A, id: caseId, name: "Neuer Vorgang" })
    );
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("XDOMEA-Import"));
    const form = screen.getByText("Paket-Datei (ZIP)").closest(".inline-form") as HTMLElement;
    const file = new File(["PK-zip"], "abgabe.zip", { type: "application/zip" });
    await user.upload(within(form).getByLabelText("Paket-Datei (ZIP)"), file);
    await user.selectOptions(
      within(form).getByLabelText("Prozessdefinition für die neue Umlaufmappe"),
      "7"
    );
    await user.click(within(form).getByText("Paket importieren"));

    await waitFor(() =>
      expect(importXdomeaIntoCaseMock).toHaveBeenCalledWith("token-123", {
        file,
        folderId: "root",
        processDefinitionId: 7,
      })
    );
    // Navigated straight into the newly created case (14.2, Post-Roadmap
    // Phase 42 Session 1) instead of staying on the list.
    await waitFor(() => expect(getCaseMock).toHaveBeenCalledWith("token-123", "case-new-1"));
    expect(await screen.findByRole("heading", { name: "Neuer Vorgang" })).toBeInTheDocument();
  });

  it("adds a case to favorites via the star toggle in the list view", async () => {
    const user = userEvent.setup();
    renderPane();

    await user.click(
      await screen.findByRole("button", { name: '"Umlaufmappe A" zu Favoriten hinzufügen' })
    );

    await waitFor(() =>
      expect(addFavoriteMock).toHaveBeenCalledWith("token-123", {
        user_id: "alice",
        object_type: "case",
        object_id: "case-1",
      })
    );
  });

  it("removes a case from favorites via the star toggle once it is already favorited", async () => {
    listFavoritesMock.mockResolvedValue([
      {
        id: "fav-1",
        user_id: "alice",
        object_type: "case",
        object_id: "case-1",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    const user = userEvent.setup();
    renderPane();

    await user.click(
      await screen.findByRole("button", { name: '"Umlaufmappe A" aus Favoriten entfernen' })
    );

    await waitFor(() =>
      expect(removeFavoriteMock).toHaveBeenCalledWith("token-123", {
        user_id: "alice",
        object_type: "case",
        object_id: "case-1",
      })
    );
  });

  it("toggles the favorite star from within the case detail view too", async () => {
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("Umlaufmappe A (2026-001)"));
    await user.click(
      await screen.findByRole("button", { name: '"Umlaufmappe A" zu Favoriten hinzufügen' })
    );

    await waitFor(() =>
      expect(addFavoriteMock).toHaveBeenCalledWith("token-123", {
        user_id: "alice",
        object_type: "case",
        object_id: "case-1",
      })
    );
  });

  it("opens the case detail view directly when openCaseId is set", async () => {
    renderPane(vi.fn(), "case-1");

    expect(await screen.findByRole("heading", { name: "Umlaufmappe A" })).toBeInTheDocument();
  });

  it("creates a new case via XJustiz import with the selected process definition", async () => {
    listProcessDefinitionsMock.mockResolvedValue([{ id: 9, name: "Justizprozess", version: 1 }]);
    importXjustizIntoCaseMock.mockResolvedValue({
      case_id: "case-new-2",
      case_created: true,
      akte_anzeigename: "Neue Akte",
      document_ids: ["new-doc-4"],
    });
    getCaseMock.mockImplementation((_token: string, caseId: string) =>
      Promise.resolve({ ...CASE_A, id: caseId, name: "Neue Akte" })
    );
    const user = userEvent.setup();
    renderPane();

    await user.click(await screen.findByText("XJustiz-Import"));
    const form = screen.getByText("Paket-Datei (ZIP)").closest(".inline-form") as HTMLElement;
    const file = new File(["PK-zip"], "xjustiz.zip", { type: "application/zip" });
    await user.upload(within(form).getByLabelText("Paket-Datei (ZIP)"), file);
    await user.selectOptions(
      within(form).getByLabelText("Prozessdefinition für die neue Umlaufmappe"),
      "9"
    );
    await user.click(within(form).getByText("Paket importieren"));

    await waitFor(() =>
      expect(importXjustizIntoCaseMock).toHaveBeenCalledWith("token-123", {
        file,
        folderId: "root",
        processDefinitionId: 9,
      })
    );
    await waitFor(() => expect(getCaseMock).toHaveBeenCalledWith("token-123", "case-new-2"));
    expect(await screen.findByRole("heading", { name: "Neue Akte" })).toBeInTheDocument();
  });
});
