import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FolderRetentionModal } from "@/components/FolderRetentionModal";
import { I18nProvider } from "@/i18n";
import type { Folder } from "@/lib/api";

const listFolderLegalHoldsMock = vi.fn();
const createFolderLegalHoldMock = vi.fn();
const releaseFolderLegalHoldMock = vi.fn();
const putFolderRetentionMock = vi.fn();
const getFolderRetentionConfigMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listFolderLegalHolds: (...args: unknown[]) => listFolderLegalHoldsMock(...args),
    createFolderLegalHold: (...args: unknown[]) => createFolderLegalHoldMock(...args),
    releaseFolderLegalHold: (...args: unknown[]) => releaseFolderLegalHoldMock(...args),
    putFolderRetention: (...args: unknown[]) => putFolderRetentionMock(...args),
    getFolderRetentionConfig: (...args: unknown[]) => getFolderRetentionConfigMock(...args),
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

const FOLDER: Folder = {
  id: "folder-1",
  name: "Projektakte",
  parent_id: "root",
  object_type_id: null,
  attributes: {},
  deleted_at: null,
  deleted_by: null,
  retention_until: null,
  full_deletion: false,
  pending_deletion_reason: null,
};

function renderModal(folder: Folder = FOLDER, onClose = vi.fn()) {
  return render(
    <I18nProvider>
      <FolderRetentionModal folder={folder} onClose={onClose} />
    </I18nProvider>
  );
}

describe("FolderRetentionModal", () => {
  beforeEach(() => {
    listFolderLegalHoldsMock.mockReset();
    listFolderLegalHoldsMock.mockResolvedValue([]);
    createFolderLegalHoldMock.mockReset();
    releaseFolderLegalHoldMock.mockReset();
    putFolderRetentionMock.mockReset();
    getFolderRetentionConfigMock.mockReset();
    getFolderRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
  });

  it("saves a retention date and full-deletion flag with a reason", async () => {
    putFolderRetentionMock.mockResolvedValue({ ...FOLDER, full_deletion: true });

    renderModal();
    await waitFor(() =>
      expect(listFolderLegalHoldsMock).toHaveBeenCalledWith("token-123", "folder-1", true)
    );

    fireEvent.change(screen.getByLabelText("Aufbewahren bis"), {
      target: { value: "2026-12-31" },
    });
    fireEvent.click(screen.getByLabelText(/vollständig löschen/));
    fireEvent.change(screen.getByLabelText("Löschgrund"), {
      target: { value: "Aufbewahrungsfrist abgelaufen" },
    });
    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() =>
      expect(putFolderRetentionMock).toHaveBeenCalledWith("token-123", "folder-1", {
        retentionUntil: new Date("2026-12-31").toISOString(),
        fullDeletion: true,
        reason: "Aufbewahrungsfrist abgelaufen",
      })
    );
    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
  });

  it("offers a curated reason dropdown plus 'Sonstiges' free text when a catalog is configured (Phase 31)", async () => {
    getFolderRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: ["Vorgang abgeschlossen"],
      updated_at: "2026-01-01T00:00:00Z",
    });
    putFolderRetentionMock.mockResolvedValue({ ...FOLDER, full_deletion: true });

    renderModal();
    await waitFor(() => expect(getFolderRetentionConfigMock).toHaveBeenCalledWith("token-123"));

    fireEvent.click(screen.getByLabelText(/vollständig löschen/));
    expect(screen.queryByLabelText("Löschgrund (sonstiges)")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Löschgrund"), { target: { value: "__other__" } });
    fireEvent.change(screen.getByLabelText("Löschgrund (sonstiges)"), {
      target: { value: "Individueller Grund" },
    });
    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() =>
      expect(putFolderRetentionMock).toHaveBeenCalledWith("token-123", "folder-1", {
        retentionUntil: null,
        fullDeletion: true,
        reason: "Individueller Grund",
      })
    );
  });

  it("sets a legal hold and then offers to release it", async () => {
    listFolderLegalHoldsMock.mockResolvedValueOnce([]);
    createFolderLegalHoldMock.mockResolvedValue({
      id: "hold-1",
      folder_id: "folder-1",
      reason: "Rechtsstreit",
      set_by: "alice",
      set_at: "2026-01-01T00:00:00Z",
      released_by: null,
      released_at: null,
    });

    renderModal();
    await waitFor(() => expect(listFolderLegalHoldsMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Grund für Legal Hold"), {
      target: { value: "Rechtsstreit" },
    });
    fireEvent.click(screen.getByText("Legal Hold setzen"));

    await waitFor(() =>
      expect(createFolderLegalHoldMock).toHaveBeenCalledWith("token-123", {
        folderId: "folder-1",
        setBy: "alice",
        reason: "Rechtsstreit",
      })
    );
    expect(await screen.findByText(/Legal Hold aktiv/)).toBeInTheDocument();

    releaseFolderLegalHoldMock.mockResolvedValue({
      id: "hold-1",
      folder_id: "folder-1",
      reason: "Rechtsstreit",
      set_by: "alice",
      set_at: "2026-01-01T00:00:00Z",
      released_by: "alice",
      released_at: "2026-02-01T00:00:00Z",
    });

    fireEvent.click(screen.getByText("Legal Hold aufheben"));

    await waitFor(() =>
      expect(releaseFolderLegalHoldMock).toHaveBeenCalledWith("token-123", "hold-1", "alice")
    );
    expect(screen.queryByText(/Legal Hold aktiv/)).not.toBeInTheDocument();
  });

  it("closes the modal when the close button is clicked", async () => {
    const onClose = vi.fn();
    renderModal(FOLDER, onClose);
    await waitFor(() => expect(listFolderLegalHoldsMock).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("Schließen"));

    expect(onClose).toHaveBeenCalled();
  });
});
