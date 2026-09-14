import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamTaskList } from "@/components/TeamTaskList";
import { I18nProvider } from "@/i18n";

const listDirectReportsMock = vi.fn();
const listReadyTasksMock = vi.fn();
const claimTaskMock = vi.fn();
const reassignTaskMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listDirectReports: (...args: unknown[]) => listDirectReportsMock(...args),
  listReadyTasks: (...args: unknown[]) => listReadyTasksMock(...args),
  claimTask: (...args: unknown[]) => claimTaskMock(...args),
  reassignTask: (...args: unknown[]) => reassignTaskMock(...args),
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
  created_by: "carla-creator",
};

// Unclaimed, but its instance was NOT created by a direct report - must
// stay excluded (same as before Post-Roadmap Phase 35 Session 3).
const UNCLAIMED_TASK = {
  ...REPORT_TASK,
  id: "task-2",
  claimed_by: null,
  created_by: "stranger-creator",
};

const STRANGERS_TASK = {
  ...REPORT_TASK,
  id: "task-3",
  claimed_by: "not-my-report",
};

// Unclaimed, instance created by a direct report - the new attribution
// signal (ADR 0145) that makes an unclaimed task visible here.
const UNCLAIMED_REPORT_TASK = {
  ...REPORT_TASK,
  id: "task-4",
  name: "Antrag sichten",
  claimed_by: null,
  created_by: "bob",
};

describe("TeamTaskList", () => {
  beforeEach(() => {
    listDirectReportsMock.mockReset();
    listReadyTasksMock.mockReset();
    claimTaskMock.mockReset();
    reassignTaskMock.mockReset();
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

  it("shows an empty state when direct reports exist but have no attributable tasks", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([UNCLAIMED_TASK, STRANGERS_TASK]);
    renderList();

    expect(
      await screen.findByText("Keine offenen Aufgaben bei direkt unterstellten Personen.")
    ).toBeInTheDocument();
  });

  it("lists only tasks claimed by a direct report, excluding unclaimed tasks from a stranger's instance and tasks claimed by a non-report", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([REPORT_TASK, UNCLAIMED_TASK, STRANGERS_TASK]);
    renderList();

    expect(await screen.findByText("Rechnung prüfen")).toBeInTheDocument();
    expect(screen.getByText("Beansprucht von bob")).toBeInTheDocument();
    // Only one row - the other two tasks (unclaimed from a stranger's
    // instance, claimed by a non-report) must not appear.
    expect(screen.getAllByRole("row")).toHaveLength(2); // header + 1 data row
  });

  it("also lists an unclaimed task whose instance was created by a direct report", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([UNCLAIMED_REPORT_TASK]);
    renderList();

    expect(await screen.findByText("Antrag sichten")).toBeInTheDocument();
    expect(screen.getByText("Unbeansprucht (Vorgang angelegt von bob)")).toBeInTheDocument();
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

  it("assigns an unclaimed team task to a chosen principal", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([UNCLAIMED_REPORT_TASK]);
    claimTaskMock.mockResolvedValue(undefined);
    renderList();

    await screen.findByText("Antrag sichten");
    fireEvent.click(screen.getByRole("button", { name: "Zuweisen" }));
    fireEvent.change(screen.getByLabelText("Zuweisen an (Nutzername/Principal-ID)"), {
      target: { value: "dora-assignee" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Zuweisen" })[1]);

    await waitFor(() =>
      expect(claimTaskMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-4",
        principalId: "dora-assignee",
      })
    );
  });

  it("reassigns a claimed team task to a chosen principal", async () => {
    listDirectReportsMock.mockResolvedValue([
      { id: 1, principal_id: "bob", supervisor_principal_id: "supervisor-sub", created_at: "2026-01-01T00:00:00Z" },
    ]);
    listReadyTasksMock.mockResolvedValue([REPORT_TASK]);
    reassignTaskMock.mockResolvedValue(undefined);
    renderList();

    await screen.findByText("Rechnung prüfen");
    fireEvent.click(screen.getByRole("button", { name: "Neu zuweisen" }));
    fireEvent.change(screen.getByLabelText("Neu zuweisen an (Nutzername/Principal-ID)"), {
      target: { value: "erik-new" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Neu zuweisen" })[1]);

    await waitFor(() =>
      expect(reassignTaskMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
        newPrincipalId: "erik-new",
      })
    );
  });
});
