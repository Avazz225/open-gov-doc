import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TransferConsole } from "@/components/TransferConsole";
import { I18nProvider } from "@/i18n";

const listTransfersMock = vi.fn();
const listPairedInstallationsMock = vi.fn();
const createTransferMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listTransfers: (...args: unknown[]) => listTransfersMock(...args),
  listPairedInstallations: (...args: unknown[]) => listPairedInstallationsMock(...args),
  createTransfer: (...args: unknown[]) => createTransferMock(...args),
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

function renderConsole() {
  return render(
    <I18nProvider>
      <TransferConsole />
    </I18nProvider>
  );
}

const INSTALLATION = {
  id: "install-1",
  display_name: "Filiale Nord",
  base_url: "https://nord.example.test",
  created_at: "2026-01-01T10:00:00Z",
};

const RUNNING_TRANSFER = {
  id: "11111111-2222-3333-4444-555555555555",
  source_folder_id: "folder-1",
  target_installation_id: "install-1",
  dry_run: false,
  retention_days: 30,
  status: "copied",
  workflow_instance_id: "wf-1",
  documents_total: 4,
  documents_copied: 2,
  documents_verified: 0,
  error_message: null,
  created_by: "alice",
  created_at: "2026-01-01T10:00:00Z",
  updated_at: "2026-01-01T10:05:00Z",
  locked_at: "2026-01-01T10:01:00Z",
  copied_at: "2026-01-01T10:05:00Z",
  verified_at: null,
  released_at: null,
  deletion_scheduled_at: null,
  deleted_at: null,
};

describe("TransferConsole", () => {
  beforeEach(() => {
    listTransfersMock.mockReset();
    listPairedInstallationsMock.mockReset();
    createTransferMock.mockReset();
    listPairedInstallationsMock.mockResolvedValue([INSTALLATION]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows an empty state when there are no transfers yet", async () => {
    listTransfersMock.mockResolvedValue([]);
    renderConsole();

    await waitFor(() => expect(screen.getByText(/Noch keine Transfers/)).toBeInTheDocument());
  });

  it("lists transfers with resolved target installation name and progress", async () => {
    listTransfersMock.mockResolvedValue([RUNNING_TRANSFER]);
    renderConsole();

    await waitFor(() => expect(screen.getByText("Filiale Nord")).toBeInTheDocument());
    expect(screen.getByText("copied", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("2/4 kopiert, 0 verifiziert")).toBeInTheDocument();
  });

  it("expands transfer details showing the phase timeline", async () => {
    listTransfersMock.mockResolvedValue([RUNNING_TRANSFER]);
    renderConsole();

    await waitFor(() => expect(screen.getByText("Filiale Nord")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Details" }));

    expect(screen.getByText("Transfer-Details")).toBeInTheDocument();
    expect(screen.getByText(/Kopiert:/)).toBeInTheDocument();
    expect(screen.queryByText(/Verifiziert:/)).not.toBeInTheDocument();
  });

  it("starts a transfer and reloads the list", async () => {
    listTransfersMock.mockResolvedValueOnce([]).mockResolvedValueOnce([RUNNING_TRANSFER]);
    createTransferMock.mockResolvedValue({
      status: "started",
      transfer: RUNNING_TRANSFER,
      approval_request_id: null,
    });
    renderConsole();

    await waitFor(() => expect(screen.getByText(/Noch keine Transfers/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Neuer Transfer" }));
    fireEvent.change(screen.getByLabelText("Quellordner-ID"), {
      target: { value: "folder-1" },
    });
    fireEvent.change(screen.getByLabelText("Ziel-Installation"), {
      target: { value: "install-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Transfer starten" }));

    await waitFor(() =>
      expect(createTransferMock).toHaveBeenCalledWith("token-123", {
        sourceFolderId: "folder-1",
        targetInstallationId: "install-1",
        createdBy: "alice",
        dryRun: false,
        retentionDays: undefined,
      })
    );
    await waitFor(() => expect(listTransfersMock).toHaveBeenCalledTimes(2));
  });

  it("surfaces a four-eyes pending-approval notice instead of starting immediately", async () => {
    listTransfersMock.mockResolvedValue([]);
    createTransferMock.mockResolvedValue({
      status: "pending_approval",
      transfer: null,
      approval_request_id: "req-42",
    });
    renderConsole();

    await waitFor(() => expect(screen.getByText(/Noch keine Transfers/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Neuer Transfer" }));
    fireEvent.change(screen.getByLabelText("Quellordner-ID"), {
      target: { value: "folder-1" },
    });
    fireEvent.change(screen.getByLabelText("Ziel-Installation"), {
      target: { value: "install-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Transfer starten" }));

    await waitFor(() =>
      expect(screen.getByText(/Vier-Augen-Freigabe erforderlich.*req-42/)).toBeInTheDocument()
    );
  });

  it("polls for updates while mounted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    listTransfersMock.mockResolvedValue([RUNNING_TRANSFER]);
    renderConsole();

    await vi.waitFor(() => expect(listTransfersMock).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(5_000);
    expect(listTransfersMock).toHaveBeenCalledTimes(2);
  });
});
