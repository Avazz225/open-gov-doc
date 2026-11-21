import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ArchivalTransfersView } from "@/components/ArchivalTransfersView";
import { I18nProvider } from "@/i18n";

function renderView() {
  return render(
    <I18nProvider>
      <ArchivalTransfersView />
    </I18nProvider>
  );
}

const listArchivalTransfersMock = vi.fn();
const retrieveArchivalTransferMock = vi.fn();
const retryArchivalTransferMock = vi.fn();
const requestDocumentArchiveMock = vi.fn();
const listCaseArchivalTransfersMock = vi.fn();
const retryCaseArchivalTransferMock = vi.fn();
const getCaseArchivalConfigMock = vi.fn();
const updateCaseArchivalConfigMock = vi.fn();
const downloadCaseArchivalPackageMock = vi.fn();
const listCaseDestructionsMock = vi.fn();
const retryCaseDestructionMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listArchivalTransfers: (...args: unknown[]) => listArchivalTransfersMock(...args),
  retrieveArchivalTransfer: (...args: unknown[]) => retrieveArchivalTransferMock(...args),
  retryArchivalTransfer: (...args: unknown[]) => retryArchivalTransferMock(...args),
  requestDocumentArchive: (...args: unknown[]) => requestDocumentArchiveMock(...args),
  listCaseArchivalTransfers: (...args: unknown[]) => listCaseArchivalTransfersMock(...args),
  retryCaseArchivalTransfer: (...args: unknown[]) => retryCaseArchivalTransferMock(...args),
  getCaseArchivalConfig: (...args: unknown[]) => getCaseArchivalConfigMock(...args),
  updateCaseArchivalConfig: (...args: unknown[]) => updateCaseArchivalConfigMock(...args),
  downloadCaseArchivalPackage: (...args: unknown[]) => downloadCaseArchivalPackageMock(...args),
  listCaseDestructions: (...args: unknown[]) => listCaseDestructionsMock(...args),
  retryCaseDestruction: (...args: unknown[]) => retryCaseDestructionMock(...args),
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

const RELEASED_TRANSFER = {
  id: "t1",
  document_id: "doc-1",
  status: "released",
  archive_format: "pdf_a",
  encrypted: false,
  storage_object_key: "archive/doc-1/t1.pdf",
  checksum_sha256: "abc",
  error_message: null,
  locked_at: "2026-01-01T00:00:00Z",
  copied_at: "2026-01-01T00:01:00Z",
  verified_at: "2026-01-01T00:02:00Z",
  released_at: "2026-01-01T00:03:00Z",
  dehydrated_at: null,
  rehydrated_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:03:00Z",
};

const CASE_ARCHIVAL_CONFIG = {
  default_archive_after_days_closed: null,
  archive_encryption_enabled: false,
  default_retention_days_after_archived: null,
  default_full_deletion: false,
  updated_at: "2026-01-01T00:00:00Z",
};

function destructionRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-1",
    case_id: "case-1",
    status: "destroyed",
    vorgangsnummer: "2026-0042",
    mode: "tombstone",
    document_reference_count: 3,
    error_message: null,
    attempts: 0,
    next_retry_at: null,
    destroyed_at: "2026-11-21T10:00:00Z",
    created_at: "2026-11-21T09:59:00Z",
    updated_at: "2026-11-21T10:00:00Z",
    ...overrides,
  };
}

describe("ArchivalTransfersView", () => {
  beforeEach(() => {
    listArchivalTransfersMock.mockReset();
    retrieveArchivalTransferMock.mockReset();
    retryArchivalTransferMock.mockReset();
    requestDocumentArchiveMock.mockReset();
    listCaseArchivalTransfersMock.mockReset().mockResolvedValue([]);
    retryCaseArchivalTransferMock.mockReset();
    getCaseArchivalConfigMock.mockReset().mockResolvedValue(CASE_ARCHIVAL_CONFIG);
    updateCaseArchivalConfigMock.mockReset();
    downloadCaseArchivalPackageMock.mockReset();
    listCaseDestructionsMock.mockReset().mockResolvedValue([]);
    retryCaseDestructionMock.mockReset();
  });

  it("lists transfers with status and archive format", async () => {
    listArchivalTransfersMock.mockResolvedValue([RELEASED_TRANSFER]);

    renderView();

    expect(await screen.findByText("doc-1")).toBeInTheDocument();
    const row = screen.getByText("doc-1").closest("tr")!;
    expect(within(row).getByText("Archiviert")).toBeInTheDocument();
    expect(within(row).getByText("pdf_a")).toBeInTheDocument();
  });

  it("shows an unreachable state when archival-service is not reachable", async () => {
    listArchivalTransfersMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderView();

    expect(await screen.findByText(/Archival Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("shows an empty state without any transfers", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);

    renderView();

    expect(await screen.findByText("Noch keine Aussonderungs-Transfers.")).toBeInTheDocument();
  });

  it("does not offer a retrieve button for a still-pending transfer", async () => {
    listArchivalTransfersMock.mockResolvedValue([{ ...RELEASED_TRANSFER, status: "pending" }]);

    renderView();
    await screen.findByText("doc-1");

    expect(screen.queryByRole("button", { name: "Zurückholen" })).not.toBeInTheDocument();
  });

  it("retrieves a released transfer after confirmation and reloads", async () => {
    listArchivalTransfersMock
      .mockResolvedValueOnce([RELEASED_TRANSFER])
      .mockResolvedValueOnce([{ ...RELEASED_TRANSFER, rehydrated_at: "2026-01-02T00:00:00Z" }]);
    retrieveArchivalTransferMock.mockResolvedValue({
      ...RELEASED_TRANSFER,
      rehydrated_at: "2026-01-02T00:00:00Z",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderView();
    await screen.findByText("doc-1");

    fireEvent.click(screen.getByRole("button", { name: "Zurückholen" }));

    expect(retrieveArchivalTransferMock).toHaveBeenCalledWith("token-123", "t1");
    await screen.findByText("doc-1");
    expect(listArchivalTransfersMock).toHaveBeenCalledTimes(2);
  });

  it("does not retrieve when the confirmation is dismissed", async () => {
    listArchivalTransfersMock.mockResolvedValue([RELEASED_TRANSFER]);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    renderView();
    await screen.findByText("doc-1");

    fireEvent.click(screen.getByRole("button", { name: "Zurückholen" }));

    expect(retrieveArchivalTransferMock).not.toHaveBeenCalled();
  });

  it("shows the case archival config and an empty transfer list", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);

    renderView();

    expect(await screen.findByText("Umlaufmappen-Aussonderung")).toBeInTheDocument();
    expect(screen.getByText("Noch keine Umlaufmappen-Aussonderungs-Transfers.")).toBeInTheDocument();
  });

  it("lists case transfers and offers a download button only when released", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    listCaseArchivalTransfersMock.mockResolvedValue([
      {
        id: "ct1",
        case_id: "case-1",
        status: "released",
        encrypted: false,
        storage_object_key: "archive-case/case-1/ct1.zip",
        checksum_sha256: "abc",
        error_message: null,
        locked_at: "2026-01-01T00:00:00Z",
        packaged_at: "2026-01-01T00:01:00Z",
        verified_at: "2026-01-01T00:02:00Z",
        released_at: "2026-01-01T00:03:00Z",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:03:00Z",
      },
      {
        id: "ct2",
        case_id: "case-2",
        status: "packaged",
        encrypted: false,
        storage_object_key: null,
        checksum_sha256: null,
        error_message: null,
        locked_at: "2026-01-01T00:00:00Z",
        packaged_at: null,
        verified_at: null,
        released_at: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);

    renderView();

    expect(await screen.findByText("case-1")).toBeInTheDocument();
    expect(screen.getByText("case-2")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Paket herunterladen" })).toHaveLength(1);
  });

  it("downloads the case archival package", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    listCaseArchivalTransfersMock.mockResolvedValue([
      {
        id: "ct1",
        case_id: "case-1",
        status: "released",
        encrypted: false,
        storage_object_key: "archive-case/case-1/ct1.zip",
        checksum_sha256: "abc",
        error_message: null,
        locked_at: null,
        packaged_at: null,
        verified_at: null,
        released_at: "2026-01-01T00:03:00Z",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:03:00Z",
      },
    ]);
    downloadCaseArchivalPackageMock.mockResolvedValue(new Blob(["zip-bytes"]));
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    renderView();
    await screen.findByText("case-1");

    fireEvent.click(screen.getByRole("button", { name: "Paket herunterladen" }));

    await vi.waitFor(() =>
      expect(downloadCaseArchivalPackageMock).toHaveBeenCalledWith("token-123", "ct1")
    );
  });

  it("does not offer a retry button for a merely failed (retry-able) transfer", async () => {
    listArchivalTransfersMock.mockResolvedValue([{ ...RELEASED_TRANSFER, status: "failed" }]);

    renderView();
    await screen.findByText("doc-1");

    expect(screen.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
  });

  it("retries a permanently failed transfer and reloads", async () => {
    const FAILED_PERMANENT_TRANSFER = { ...RELEASED_TRANSFER, status: "failed_permanent" };
    listArchivalTransfersMock
      .mockResolvedValueOnce([FAILED_PERMANENT_TRANSFER])
      .mockResolvedValueOnce([{ ...FAILED_PERMANENT_TRANSFER, status: "pending" }]);
    retryArchivalTransferMock.mockResolvedValue({ ...FAILED_PERMANENT_TRANSFER, status: "pending" });

    renderView();
    await screen.findByText("doc-1");
    const row = screen.getByText("doc-1").closest("tr")!;
    expect(within(row).getByText("Dauerhaft fehlgeschlagen")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));

    expect(retryArchivalTransferMock).toHaveBeenCalledWith("token-123", "t1");
    await screen.findByText("doc-1");
    expect(listArchivalTransfersMock).toHaveBeenCalledTimes(2);
  });

  it("retries a permanently failed case transfer and reloads", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    const FAILED_PERMANENT_CASE = {
      id: "ct1",
      case_id: "case-1",
      status: "failed_permanent",
      encrypted: false,
      storage_object_key: null,
      checksum_sha256: null,
      error_message: "boom",
      locked_at: null,
      packaged_at: null,
      verified_at: null,
      released_at: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    listCaseArchivalTransfersMock
      .mockResolvedValueOnce([FAILED_PERMANENT_CASE])
      .mockResolvedValueOnce([{ ...FAILED_PERMANENT_CASE, status: "pending" }]);
    retryCaseArchivalTransferMock.mockResolvedValue({ ...FAILED_PERMANENT_CASE, status: "pending" });

    renderView();
    await screen.findByText("case-1");

    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));

    expect(retryCaseArchivalTransferMock).toHaveBeenCalledWith("token-123", "ct1");
    await screen.findByText("case-1");
    expect(listCaseArchivalTransfersMock).toHaveBeenCalledTimes(2);
  });

  it("saves the case archival config", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    updateCaseArchivalConfigMock.mockResolvedValue({
      ...CASE_ARCHIVAL_CONFIG,
      default_archive_after_days_closed: 90,
      archive_encryption_enabled: true,
    });

    renderView();
    await screen.findByText("Umlaufmappen-Aussonderung");

    fireEvent.change(screen.getByLabelText("Aussonderung nach Abschluss (Tage)"), {
      target: { value: "90" },
    });
    fireEvent.click(screen.getByLabelText("Aussonderungspaket verschlüsseln"));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await vi.waitFor(() =>
      expect(updateCaseArchivalConfigMock).toHaveBeenCalledWith("token-123", 90, true, {
        defaultRetentionDaysAfterArchived: null,
        defaultFullDeletion: false,
      })
    );
  });

  it("saves the case destruction schedule with the archival config (P89-S2)", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    getCaseArchivalConfigMock.mockResolvedValue({
      ...CASE_ARCHIVAL_CONFIG,
      default_retention_days_after_archived: 3650,
    });
    updateCaseArchivalConfigMock.mockResolvedValue(CASE_ARCHIVAL_CONFIG);

    renderView();
    const days = await screen.findByLabelText("Vernichtung nach Aussonderung (Tage)");
    expect(days).toHaveValue(3650);

    fireEvent.change(days, { target: { value: "" } });
    fireEvent.click(screen.getByLabelText("Vollständig löschen statt Nachweis behalten"));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await vi.waitFor(() =>
      expect(updateCaseArchivalConfigMock).toHaveBeenCalledWith("token-123", null, false, {
        defaultRetentionDaysAfterArchived: null,
        defaultFullDeletion: true,
      })
    );
  });

  it("lists destruction runs with blocked reasons and retries a failed one (P89-S2)", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    listCaseDestructionsMock.mockResolvedValue([
      destructionRun(),
      destructionRun({
        id: "run-2",
        case_id: "case-2",
        vorgangsnummer: "2026-0043",
        status: "blocked",
        mode: null,
        destroyed_at: null,
        error_message: "Dokumente in aktiver Quarantäne: doc-9",
      }),
      destructionRun({
        id: "run-3",
        case_id: "case-3",
        vorgangsnummer: null,
        status: "failed_permanent",
        mode: null,
        destroyed_at: null,
        error_message: "503",
      }),
    ]);
    retryCaseDestructionMock.mockResolvedValue(destructionRun({ id: "run-3", status: "pending" }));

    renderView();
    const section = (await screen.findByRole("heading", { name: "Vernichtung von Umlaufmappen" }))
      .closest("section") as HTMLElement;

    const destroyed = within(section).getByText("2026-0042").closest("tr") as HTMLElement;
    expect(within(destroyed).getByText("Vernichtet")).toBeInTheDocument();
    expect(within(destroyed).getByText("Nachweis")).toBeInTheDocument();
    expect(within(destroyed).queryByRole("button")).not.toBeInTheDocument();

    const blocked = within(section).getByText("2026-0043").closest("tr") as HTMLElement;
    expect(within(blocked).getByText("Blockiert (Quarantäne)")).toBeInTheDocument();
    expect(within(blocked).getByText(/doc-9/)).toBeInTheDocument();
    expect(within(blocked).queryByRole("button")).not.toBeInTheDocument();

    const failed = within(section).getByText("case-3").closest("tr") as HTMLElement;
    fireEvent.click(within(failed).getByRole("button", { name: "Erneut versuchen" }));
    await vi.waitFor(() =>
      expect(retryCaseDestructionMock).toHaveBeenCalledWith("token-123", "run-3")
    );
    await vi.waitFor(() => expect(listCaseDestructionsMock).toHaveBeenCalledTimes(2));
  });

  it("requests immediate archival for a document by ID and shows a success hint", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    requestDocumentArchiveMock.mockResolvedValue(undefined);

    renderView();
    await screen.findByText("Noch keine Aussonderungs-Transfers.");

    fireEvent.change(screen.getByLabelText("Dokument-ID"), { target: { value: "doc-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Jetzt aussondern" }));

    await vi.waitFor(() =>
      expect(requestDocumentArchiveMock).toHaveBeenCalledWith("token-123", "doc-42")
    );
    expect(
      await screen.findByText(/zur sofortigen Aussonderung vorgemerkt/)
    ).toBeInTheDocument();
  });

  it("shows the backend error message when requesting immediate archival fails", async () => {
    listArchivalTransfersMock.mockResolvedValue([]);
    const { ApiError } = await import("@/lib/api");
    requestDocumentArchiveMock.mockRejectedValue(new ApiError(404, "Dokument nicht gefunden"));

    renderView();
    await screen.findByText("Noch keine Aussonderungs-Transfers.");

    fireEvent.change(screen.getByLabelText("Dokument-ID"), { target: { value: "unknown-doc" } });
    fireEvent.click(screen.getByRole("button", { name: "Jetzt aussondern" }));

    expect(await screen.findByText("Dokument nicht gefunden")).toBeInTheDocument();
  });
});
