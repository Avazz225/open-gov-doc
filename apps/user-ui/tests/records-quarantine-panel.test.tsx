import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecordsQuarantinePanel } from "@/components/RecordsQuarantinePanel";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary } from "@/lib/api";

const listRecordsQuarantineMock = vi.fn();
const createRecordsQuarantineMock = vi.fn();
const releaseRecordsQuarantineMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listRecordsQuarantine: (...args: unknown[]) => listRecordsQuarantineMock(...args),
    createRecordsQuarantine: (...args: unknown[]) => createRecordsQuarantineMock(...args),
    releaseRecordsQuarantine: (...args: unknown[]) => releaseRecordsQuarantineMock(...args),
  };
});

let mockPermissions = ["admin.records_quarantine"];
vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      permissions: mockPermissions,
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
  derivation_type: null,
};

function renderPanel(document: DocumentSummary = DOCUMENT) {
  return render(
    <I18nProvider>
      <RecordsQuarantinePanel document={document} />
    </I18nProvider>
  );
}

describe("RecordsQuarantinePanel (Post-Roadmap Phase 31 Session 5, ADR 0116)", () => {
  beforeEach(() => {
    mockPermissions = ["admin.records_quarantine"];
    listRecordsQuarantineMock.mockReset();
    listRecordsQuarantineMock.mockResolvedValue([]);
    createRecordsQuarantineMock.mockReset();
    releaseRecordsQuarantineMock.mockReset();
  });

  it("renders nothing for a viewer without admin.records_quarantine", async () => {
    mockPermissions = [];
    const { container } = renderPanel();
    await waitFor(() => expect(listRecordsQuarantineMock).not.toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("sets a quarantine and then offers to release it", async () => {
    listRecordsQuarantineMock.mockResolvedValueOnce([]);
    createRecordsQuarantineMock.mockResolvedValue({
      id: "q-1",
      document_id: "doc-1",
      reason: "Aussonderung geprüft",
      auto_delete_at: null,
      set_by: "alice",
      set_at: "2026-01-01T00:00:00Z",
      released_by: null,
      released_at: null,
    });

    renderPanel();
    await waitFor(() =>
      expect(listRecordsQuarantineMock).toHaveBeenCalledWith("token-123", "doc-1", true)
    );

    fireEvent.change(screen.getByLabelText("Grund"), {
      target: { value: "Aussonderung geprüft" },
    });
    fireEvent.click(screen.getByText("In Quarantäne verschieben"));

    await waitFor(() =>
      expect(createRecordsQuarantineMock).toHaveBeenCalledWith("token-123", {
        documentId: "doc-1",
        setBy: "alice",
        reason: "Aussonderung geprüft",
        autoDeleteAt: null,
      })
    );
    expect(await screen.findByText(/In Quarantäne \(gesetzt von alice\)/)).toBeInTheDocument();

    releaseRecordsQuarantineMock.mockResolvedValue({
      id: "q-1",
      document_id: "doc-1",
      reason: "Aussonderung geprüft",
      auto_delete_at: null,
      set_by: "alice",
      set_at: "2026-01-01T00:00:00Z",
      released_by: "alice",
      released_at: "2026-02-01T00:00:00Z",
    });

    fireEvent.click(screen.getByText("Quarantäne aufheben"));

    await waitFor(() =>
      expect(releaseRecordsQuarantineMock).toHaveBeenCalledWith("token-123", "q-1", "alice")
    );
    expect(screen.queryByText(/In Quarantäne \(gesetzt von alice\)/)).not.toBeInTheDocument();
  });

  it("shows the active quarantine from the initial load, including the auto-delete date", async () => {
    listRecordsQuarantineMock.mockResolvedValue([
      {
        id: "q-2",
        document_id: "doc-1",
        reason: null,
        auto_delete_at: "2026-06-15T00:00:00Z",
        set_by: "bob",
        set_at: "2026-01-01T00:00:00Z",
        released_by: null,
        released_at: null,
      },
    ]);

    renderPanel();

    expect(await screen.findByText(/In Quarantäne \(gesetzt von bob\)/)).toBeInTheDocument();
    expect(screen.getByText(/automatische Löschung am 2026-06-15/)).toBeInTheDocument();
    expect(screen.getByText("Quarantäne aufheben")).toBeInTheDocument();
  });
});
