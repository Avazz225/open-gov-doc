import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RetentionPanel } from "@/components/RetentionPanel";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary } from "@/lib/api";

const listLegalHoldsMock = vi.fn();
const createLegalHoldMock = vi.fn();
const releaseLegalHoldMock = vi.fn();
const putDocumentRetentionMock = vi.fn();
const getRetentionConfigMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listLegalHolds: (...args: unknown[]) => listLegalHoldsMock(...args),
    createLegalHold: (...args: unknown[]) => createLegalHoldMock(...args),
    releaseLegalHold: (...args: unknown[]) => releaseLegalHoldMock(...args),
    putDocumentRetention: (...args: unknown[]) => putDocumentRetentionMock(...args),
    getRetentionConfig: (...args: unknown[]) => getRetentionConfigMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      // RBAC (Post-Roadmap Phase 19 Session 10, ADR 0075): the Legal Hold
      // button is only active for principals with `admin.legal_hold`.
      permissions: ["admin.legal_hold"],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const DOCUMENT: DocumentSummary = {
  id: "doc-1",
  title: "Vertrag",
  folder_id: null,
  object_type_id: null,
  attributes: {},
  current_version_number: 1,
  deleted_at: null,
  deleted_by: null,
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  retention_until: null,
  full_deletion: false,
  pending_deletion_reason: null,
  registered_at: "2026-01-01T00:00:00Z",
  classification_level: null,
};

function renderPanel(document: DocumentSummary = DOCUMENT) {
  return render(
    <I18nProvider>
      <RetentionPanel document={document} />
    </I18nProvider>
  );
}

describe("RetentionPanel", () => {
  beforeEach(() => {
    listLegalHoldsMock.mockReset();
    listLegalHoldsMock.mockResolvedValue([]);
    createLegalHoldMock.mockReset();
    releaseLegalHoldMock.mockReset();
    putDocumentRetentionMock.mockReset();
    getRetentionConfigMock.mockReset();
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
  });

  it("saves a retention date and full-deletion flag with a reason", async () => {
    putDocumentRetentionMock.mockResolvedValue({ ...DOCUMENT, full_deletion: true });

    renderPanel();
    await waitFor(() => expect(listLegalHoldsMock).toHaveBeenCalledWith("token-123", "doc-1", true));

    fireEvent.change(screen.getByLabelText("Aufbewahren bis"), {
      target: { value: "2026-12-31" },
    });
    fireEvent.click(screen.getByLabelText(/vollständig löschen/));
    fireEvent.change(screen.getByLabelText("Löschgrund"), {
      target: { value: "Aufbewahrungsfrist abgelaufen" },
    });
    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() =>
      expect(putDocumentRetentionMock).toHaveBeenCalledWith("token-123", "doc-1", {
        retentionUntil: new Date("2026-12-31").toISOString(),
        fullDeletion: true,
        reason: "Aufbewahrungsfrist abgelaufen",
      })
    );
    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
  });

  it("offers a curated reason dropdown plus 'Sonstiges' free text when a catalog is configured (Phase 31)", async () => {
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: ["Aufbewahrungsfrist abgelaufen", "Dublette"],
      updated_at: "2026-01-01T00:00:00Z",
    });
    putDocumentRetentionMock.mockResolvedValue({ ...DOCUMENT, full_deletion: true });

    renderPanel();
    await waitFor(() => expect(getRetentionConfigMock).toHaveBeenCalledWith("token-123"));

    fireEvent.click(screen.getByLabelText(/vollständig löschen/));
    // No free-text input yet - the catalog dropdown is the default view.
    expect(screen.queryByLabelText("Löschgrund (sonstiges)")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Löschgrund"), { target: { value: "Dublette" } });
    fireEvent.click(screen.getByText("Speichern"));
    await waitFor(() =>
      expect(putDocumentRetentionMock).toHaveBeenCalledWith("token-123", "doc-1", {
        retentionUntil: null,
        fullDeletion: true,
        reason: "Dublette",
      })
    );

    fireEvent.change(screen.getByLabelText("Löschgrund"), { target: { value: "__other__" } });
    const freeTextInput = screen.getByLabelText("Löschgrund (sonstiges)");
    fireEvent.change(freeTextInput, { target: { value: "Individueller Grund" } });
    fireEvent.click(screen.getByText("Speichern"));
    await waitFor(() =>
      expect(putDocumentRetentionMock).toHaveBeenCalledWith("token-123", "doc-1", {
        retentionUntil: null,
        fullDeletion: true,
        reason: "Individueller Grund",
      })
    );
  });

  it("sets a legal hold and then offers to release it", async () => {
    listLegalHoldsMock.mockResolvedValueOnce([]);
    createLegalHoldMock.mockResolvedValue({
      id: "hold-1",
      document_id: "doc-1",
      reason: "Rechtsstreit",
      set_by: "alice",
      set_at: "2026-01-01T00:00:00Z",
      released_by: null,
      released_at: null,
    });

    renderPanel();
    await waitFor(() => expect(listLegalHoldsMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Grund für Legal Hold"), {
      target: { value: "Rechtsstreit" },
    });
    fireEvent.click(screen.getByText("Legal Hold setzen"));

    await waitFor(() =>
      expect(createLegalHoldMock).toHaveBeenCalledWith("token-123", {
        documentId: "doc-1",
        setBy: "alice",
        reason: "Rechtsstreit",
      })
    );
    expect(await screen.findByText(/Legal Hold aktiv/)).toBeInTheDocument();

    releaseLegalHoldMock.mockResolvedValue({
      id: "hold-1",
      document_id: "doc-1",
      reason: "Rechtsstreit",
      set_by: "alice",
      set_at: "2026-01-01T00:00:00Z",
      released_by: "alice",
      released_at: "2026-02-01T00:00:00Z",
    });

    fireEvent.click(screen.getByText("Legal Hold aufheben"));

    await waitFor(() =>
      expect(releaseLegalHoldMock).toHaveBeenCalledWith("token-123", "hold-1", "alice")
    );
    expect(screen.queryByText(/Legal Hold aktiv/)).not.toBeInTheDocument();
  });

  it("shows the active legal hold from the initial load with a release button", async () => {
    listLegalHoldsMock.mockResolvedValue([
      {
        id: "hold-2",
        document_id: "doc-1",
        reason: null,
        set_by: "bob",
        set_at: "2026-01-01T00:00:00Z",
        released_by: null,
        released_at: null,
      },
    ]);

    renderPanel();

    expect(await screen.findByText(/Legal Hold aktiv \(gesetzt von bob\)/)).toBeInTheDocument();
    expect(screen.getByText("Legal Hold aufheben")).toBeInTheDocument();
  });
});
