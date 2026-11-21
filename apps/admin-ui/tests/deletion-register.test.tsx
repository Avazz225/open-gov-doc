import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeletionRegister } from "@/components/DeletionRegister";
import { I18nProvider } from "@/i18n";

function renderDeletionRegister() {
  return render(
    <I18nProvider>
      <DeletionRegister />
    </I18nProvider>
  );
}

const listDeletionRegisterMock = vi.fn();
const listFolderDeletionRegisterMock = vi.fn();
const listCaseDeletionRegisterMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listDeletionRegister: (...args: unknown[]) => listDeletionRegisterMock(...args),
  listFolderDeletionRegister: (...args: unknown[]) => listFolderDeletionRegisterMock(...args),
  listCaseDeletionRegister: (...args: unknown[]) => listCaseDeletionRegisterMock(...args),
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
      user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
      permissions: [],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("DeletionRegister", () => {
  beforeEach(() => {
    listDeletionRegisterMock.mockReset();
    listFolderDeletionRegisterMock.mockReset();
    listFolderDeletionRegisterMock.mockResolvedValue([]);
    listCaseDeletionRegisterMock.mockReset().mockResolvedValue([]);
  });

  it("shows an empty-state message when there are no entries", async () => {
    listDeletionRegisterMock.mockResolvedValue([]);

    renderDeletionRegister();

    expect(await screen.findByText("Noch keine Einträge.")).toBeInTheDocument();
  });

  it("lists document and folder entries together with resolved trigger labels", async () => {
    listDeletionRegisterMock.mockResolvedValue([
      {
        id: "reg-1",
        document_id: "doc-1",
        trigger: "forced_deletion",
        reason: "Aufbewahrungsfrist abgelaufen",
        triggered_by: "alice",
        occurred_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "reg-2",
        document_id: "doc-2",
        trigger: "trash_expiry",
        reason: null,
        triggered_by: null,
        occurred_at: "2026-01-02T00:00:00Z",
      },
    ]);
    listFolderDeletionRegisterMock.mockResolvedValue([
      {
        id: "reg-3",
        folder_id: "folder-1",
        trigger: "forced_deletion",
        reason: "Aufgeräumt",
        triggered_by: "bob",
        occurred_at: "2026-01-03T00:00:00Z",
      },
    ]);

    renderDeletionRegister();

    expect(await screen.findByText("doc-1")).toBeInTheDocument();
    expect(screen.getAllByText("Zwangslöschung")).toHaveLength(2);
    expect(screen.getByText("Aufbewahrungsfrist abgelaufen")).toBeInTheDocument();
    expect(screen.getByText("doc-2")).toBeInTheDocument();
    expect(screen.getByText("Papierkorb-Frist abgelaufen")).toBeInTheDocument();
    expect(screen.getByText("folder-1")).toBeInTheDocument();
    expect(screen.getByText("Aufgeräumt")).toBeInTheDocument();
    expect(screen.getAllByText("Dokument")).toHaveLength(2);
    expect(screen.getByText("Ordner")).toBeInTheDocument();
  });

  it("shows an unreachable state when document-service is not reachable", async () => {
    listDeletionRegisterMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderDeletionRegister();

    expect(await screen.findByText("Document Service nicht erreichbar.")).toBeInTheDocument();
  });

  it("lists destroyed case binders with their Vorgangsnummer and mode (P89-S2)", async () => {
    listDeletionRegisterMock.mockResolvedValue([
      {
        id: "reg-1",
        document_id: "doc-1",
        trigger: "manual_purge",
        reason: null,
        triggered_by: "alice",
        occurred_at: "2026-01-01T00:00:00Z",
      },
    ]);
    listCaseDeletionRegisterMock.mockResolvedValue([
      {
        id: "case-reg-1",
        case_id: "case-1",
        vorgangsnummer: "2026-0042",
        trigger: "retention_expiry",
        mode: "tombstone",
        document_reference_count: 3,
        reason: null,
        triggered_by: "archival-service",
        occurred_at: "2026-11-21T10:00:00Z",
      },
      {
        id: "case-reg-2",
        case_id: "case-2",
        vorgangsnummer: null,
        trigger: "restore_reconciliation",
        mode: "deleted",
        document_reference_count: 0,
        reason: "Restore-Abgleich",
        triggered_by: "system:restore-reconciliation",
        occurred_at: "2026-11-22T10:00:00Z",
      },
    ]);

    renderDeletionRegister();

    const row = (await screen.findByText("2026-0042")).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("Umlaufmappe");
    expect(row).toHaveTextContent("case-1");
    expect(row).toHaveTextContent("Aufbewahrung abgelaufen (Nachweis bleibt)");
    expect(row).toHaveTextContent("3 Dokumentverweise entfernt");
    // Without a Vorgangsnummer the case id is the label; trigger and reason
    // both read "Restore-Abgleich" here.
    const reconciled = screen.getByText("case-2").closest("tr") as HTMLElement;
    expect(within(reconciled).getAllByText("Restore-Abgleich")).toHaveLength(2);
    expect(screen.getByText("Manuell endgültig gelöscht")).toBeInTheDocument();
  });

  it("still lists documents and folders when case-service is unreachable", async () => {
    listDeletionRegisterMock.mockResolvedValue([
      {
        id: "reg-1",
        document_id: "doc-1",
        trigger: "forced_deletion",
        reason: null,
        triggered_by: null,
        occurred_at: "2026-01-01T00:00:00Z",
      },
    ]);
    listCaseDeletionRegisterMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderDeletionRegister();

    expect(await screen.findByText("doc-1")).toBeInTheDocument();
    expect(
      screen.getByText("Case Service nicht erreichbar - Umlaufmappen fehlen in der Liste.")
    ).toBeInTheDocument();
  });
});
