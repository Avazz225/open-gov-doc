import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReportsView } from "@/components/ReportsView";
import { I18nProvider } from "@/i18n";

function renderReportsView() {
  return render(
    <I18nProvider>
      <ReportsView />
    </I18nProvider>
  );
}

const getDocumentVolumeReportMock = vi.fn();
const getOpenWorkflowTasksReportMock = vi.fn();
const getStorageUsageReportMock = vi.fn();
const getUserActivityReportMock = vi.fn();
const exportReportMock = vi.fn();
const listReportSchedulesMock = vi.fn();
const createReportScheduleMock = vi.fn();
const deleteReportScheduleMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getDocumentVolumeReport: (...args: unknown[]) => getDocumentVolumeReportMock(...args),
  getOpenWorkflowTasksReport: (...args: unknown[]) => getOpenWorkflowTasksReportMock(...args),
  getStorageUsageReport: (...args: unknown[]) => getStorageUsageReportMock(...args),
  getUserActivityReport: (...args: unknown[]) => getUserActivityReportMock(...args),
  exportReport: (...args: unknown[]) => exportReportMock(...args),
  listReportSchedules: (...args: unknown[]) => listReportSchedulesMock(...args),
  createReportSchedule: (...args: unknown[]) => createReportScheduleMock(...args),
  deleteReportSchedule: (...args: unknown[]) => deleteReportScheduleMock(...args),
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

describe("ReportsView", () => {
  beforeEach(() => {
    getDocumentVolumeReportMock.mockReset().mockResolvedValue([]);
    getOpenWorkflowTasksReportMock.mockReset().mockResolvedValue([]);
    getStorageUsageReportMock.mockReset().mockResolvedValue([]);
    getUserActivityReportMock.mockReset().mockResolvedValue([]);
    exportReportMock.mockReset();
    listReportSchedulesMock.mockReset().mockResolvedValue([]);
    createReportScheduleMock.mockReset();
    deleteReportScheduleMock.mockReset();
  });

  it("shows empty-state messages for all sections when there is no data", async () => {
    renderReportsView();

    expect(await screen.findAllByText("Keine Daten für den gewählten Zeitraum/Filter.")).toHaveLength(4);
    expect(await screen.findByText("Keine geplanten Berichte.")).toBeInTheDocument();
  });

  it("renders document volume entries", async () => {
    getDocumentVolumeReportMock.mockResolvedValue([
      { period: "2026-08-01", folder_id: "folder-1", count: 3 },
    ]);

    renderReportsView();

    expect(await screen.findByText("2026-08-01")).toBeInTheDocument();
    expect(screen.getByText("folder-1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders open workflow tasks entries", async () => {
    getOpenWorkflowTasksReportMock.mockResolvedValue([
      {
        instance_id: "inst-1",
        business_key: "case-1",
        task_id: "task-1",
        task_name: "Prüfen",
        lane: "Fachabteilung",
      },
    ]);

    renderReportsView();

    expect(await screen.findByText("inst-1")).toBeInTheDocument();
    expect(screen.getByText("case-1")).toBeInTheDocument();
    expect(screen.getByText("Prüfen")).toBeInTheDocument();
    expect(screen.getByText("Fachabteilung")).toBeInTheDocument();
  });

  it("renders storage usage entries", async () => {
    getStorageUsageReportMock.mockResolvedValue([
      { backend: "local", object_count: 42, total_size_bytes: 123456 },
    ]);

    renderReportsView();

    expect(await screen.findByText("local")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders user activity entries", async () => {
    getUserActivityReportMock.mockResolvedValue([
      { actor: "alice", event_type: "document.created", count: 5 },
    ]);

    renderReportsView();

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByText("document.created")).toBeInTheDocument();
  });

  it("shows an error message when a report fails to load", async () => {
    getDocumentVolumeReportMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderReportsView();

    expect(await screen.findByText("Bericht konnte nicht geladen werden")).toBeInTheDocument();
  });

  it("triggers a CSV export for a report section", async () => {
    const blob = new Blob(["a,b"], { type: "text/csv" });
    exportReportMock.mockResolvedValue(blob);
    const user = userEvent.setup();
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    renderReportsView();

    const exportButtons = await screen.findAllByText("CSV-Export");
    await user.click(exportButtons[0]);

    await waitFor(() => expect(exportReportMock).toHaveBeenCalled());
    expect(exportReportMock.mock.calls[0][1]).toBe("document_volume");
    expect(exportReportMock.mock.calls[0][2]).toBe("csv");
    expect(createObjectURL).toHaveBeenCalledWith(blob);

    vi.unstubAllGlobals();
  });

  it("lists existing report schedules and deletes one", async () => {
    listReportSchedulesMock.mockResolvedValue([
      {
        id: "sched-1",
        report_type: "storage_usage",
        format: "pdf",
        frequency: "weekly",
        recipient_email: "ops@example.com",
        next_run_at: "2026-08-10T00:00:00Z",
      },
    ]);
    deleteReportScheduleMock.mockResolvedValue(undefined);
    const user = userEvent.setup();

    renderReportsView();

    expect(await screen.findByText("ops@example.com")).toBeInTheDocument();

    await user.click(screen.getByText("Löschen"));

    await waitFor(() => expect(deleteReportScheduleMock).toHaveBeenCalledWith("token-123", "sched-1"));
  });

  it("creates a new report schedule via the form", async () => {
    listReportSchedulesMock.mockResolvedValue([]);
    createReportScheduleMock.mockResolvedValue({
      id: "sched-2",
      report_type: "document_volume",
      format: "csv",
      frequency: "weekly",
      recipient_email: "new@example.com",
      next_run_at: "2026-08-11T00:00:00Z",
    });
    const user = userEvent.setup();

    renderReportsView();

    const emailInput = await screen.findByPlaceholderText("Empfänger-E-Mail");
    await user.type(emailInput, "new@example.com");
    await user.click(screen.getByText("Planung anlegen"));

    await waitFor(() =>
      expect(createReportScheduleMock).toHaveBeenCalledWith("token-123", {
        reportType: "document_volume",
        format: "csv",
        frequency: "weekly",
        recipientEmail: "new@example.com",
      })
    );
  });
});
