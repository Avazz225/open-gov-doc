import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamTaskList } from "@/components/TeamTaskList";
import { I18nProvider } from "@/i18n";

const listDirectReportsMock = vi.fn();
const listReadyTasksMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listDirectReports: (...args: unknown[]) => listDirectReportsMock(...args),
  listReadyTasks: (...args: unknown[]) => listReadyTasksMock(...args),
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
    user: { sub: "supervisor-sub", username: "alice", email: null, realm_roles: [] },
    permissions: [],
    accessToken: "token-123",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

const onOpenInstanceMock = vi.fn();

function renderList() {
  return render(
    <I18nProvider>
      <TeamTaskList onOpenInstance={onOpenInstanceMock} />
    </I18nProvider>
  );
}

const REPORT_TASK = {
  id: "task-1",
  name: "Rechnung prüfen",
  lane: "Sachbearbeitung",
  data: {},
  extensions: {},
  claimed_by: "bob",
  grant_kind: null,
  instance_id: "instance-1",
  process_definition_id: 5,
  business_key: "case-42",
};

const UNCLAIMED_TASK = {
  ...REPORT_TASK,
  id: "task-2",
  claimed_by: null,
};

const STRANGERS_TASK = {
  ...REPORT_TASK,
  id: "task-3",
  claimed_by: "not-my-report",
};

describe("TeamTaskList", () => {
  beforeEach(() => {
    listDirectReportsMock.mockReset();
    listReadyTasksMock.mockReset();
    onOpenInstanceMock.mockReset();
  });

  it("shows a dedicated empty state when there are no direct reports at all", async () => {
    listDirectReportsMock.mockResolvedValue([]);
    listReadyTasksMock.mockResolvedValue([]);
    renderList();

    await waitFor(() => expect(listDirectReportsMock).toHaveBeenCalledWith("token-123", "supervisor-sub"));
    expect(
      await screen.findByText("Keine direkt unterstellten Personen konfiguriert (Organisations-Hierarchie).")
    ).toBeInTheDocument();
  });

  it("shows an empty state when direct reports exist but have no open claimed tasks", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([UNCLAIMED_TASK, STRANGERS_TASK]);
    renderList();

    expect(
      await screen.findByText("Keine offenen Aufgaben bei direkt unterstellten Personen.")
    ).toBeInTheDocument();
  });

  it("lists only tasks claimed by a direct report, excluding unclaimed and non-report tasks", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([REPORT_TASK, UNCLAIMED_TASK, STRANGERS_TASK]);
    renderList();

    expect(await screen.findByText("Rechnung prüfen")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    // Only one row - the other two tasks (unclaimed, claimed by a
    // non-report) must not appear.
    expect(screen.getAllByRole("row")).toHaveLength(2); // header + 1 data row
  });

  it("opens the Vorgang for a listed team task", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([REPORT_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Vorgang öffnen" }));

    expect(onOpenInstanceMock).toHaveBeenCalledWith("instance-1");
  });
});
