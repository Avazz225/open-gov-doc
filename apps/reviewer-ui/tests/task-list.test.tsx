import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskList } from "@/components/TaskList";
import { I18nProvider } from "@/i18n";

const listReadyTasksMock = vi.fn();
const completeTaskMock = vi.fn();
const listActiveDelegationsForDeputyMock = vi.fn();
const claimTaskMock = vi.fn();
const releaseTaskClaimMock = vi.fn();
const createTaskOrgHierarchyGrantMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listReadyTasks: (...args: unknown[]) => listReadyTasksMock(...args),
  completeTask: (...args: unknown[]) => completeTaskMock(...args),
  listActiveDelegationsForDeputy: (...args: unknown[]) =>
    listActiveDelegationsForDeputyMock(...args),
  claimTask: (...args: unknown[]) => claimTaskMock(...args),
  releaseTaskClaim: (...args: unknown[]) => releaseTaskClaimMock(...args),
  createTaskOrgHierarchyGrant: (...args: unknown[]) => createTaskOrgHierarchyGrantMock(...args),
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

const onOpenInstanceMock = vi.fn();

function renderList() {
  return render(
    <I18nProvider>
      <TaskList onOpenInstance={onOpenInstanceMock} />
    </I18nProvider>
  );
}

const MANUAL_TASK = {
  id: "task-1",
  name: "Rechnung prüfen",
  lane: "Sachbearbeitung",
  data: {},
  extensions: {},
  claimed_by: null,
  grant_kind: null,
  instance_id: "instance-1",
  process_definition_id: 5,
  business_key: "case-42",
};

const SIGNATURE_TASK = {
  id: "task-2",
  name: "Freigabe unterschreiben",
  lane: null,
  data: { document_id: "doc-1" },
  extensions: { taskType: "signature", requiredLevel: "aes" },
  claimed_by: null,
  grant_kind: null,
  instance_id: "instance-2",
  process_definition_id: 6,
  business_key: null,
};

describe("TaskList", () => {
  beforeEach(() => {
    listReadyTasksMock.mockReset();
    completeTaskMock.mockReset();
    listActiveDelegationsForDeputyMock.mockReset();
    listActiveDelegationsForDeputyMock.mockResolvedValue([]);
    claimTaskMock.mockReset();
    releaseTaskClaimMock.mockReset();
    createTaskOrgHierarchyGrantMock.mockReset();
    onOpenInstanceMock.mockReset();
  });

  it("shows an empty state when there are no ready tasks", async () => {
    listReadyTasksMock.mockResolvedValue([]);
    renderList();

    await waitFor(() => expect(screen.getByText(/Keine offenen Aufgaben/)).toBeInTheDocument());
  });

  it("lists ready tasks with their process/business-key context", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    expect(screen.getByText("case-42")).toBeInTheDocument();
    expect(screen.getByText("Sachbearbeitung")).toBeInTheDocument();
  });

  it("opens the Vorgang detail view for a task's instance (Phase 29, ADR 0109)", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Vorgang öffnen" }));

    expect(onOpenInstanceMock).toHaveBeenCalledWith("instance-1");
  });

  it("marks a signature task and requires a signature ID to complete it", async () => {
    listReadyTasksMock.mockResolvedValue([SIGNATURE_TASK]);
    renderList();

    await waitFor(() =>
      expect(screen.getByText("Freigabe unterschreiben")).toBeInTheDocument()
    );
    expect(screen.getByText("Signatur erforderlich")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    expect(screen.getByLabelText("Signatur-ID")).toBeInTheDocument();
  });

  it("completes a manual task and reloads the list", async () => {
    listReadyTasksMock.mockResolvedValueOnce([MANUAL_TASK]).mockResolvedValueOnce([]);
    completeTaskMock.mockResolvedValue(undefined);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByRole("button", { name: "Abschließen" }));

    await waitFor(() =>
      expect(completeTaskMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
        completedBy: "alice",
        data: {},
        signatureId: undefined,
      })
    );
    await waitFor(() => expect(listReadyTasksMock).toHaveBeenCalledTimes(2));
  });

  it("shows an error instead of submitting when the extra data field is not valid JSON", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.change(screen.getByLabelText(/Zusätzliche Prozessdaten/), {
      target: { value: "{not json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Abschließen" }));

    await waitFor(() => expect(screen.getByText("Kein gültiges JSON")).toBeInTheDocument());
    expect(completeTaskMock).not.toHaveBeenCalled();
  });

  // --- Absence deputization (4.4a, P14-S11) ---------------------------------

  it("does not show an 'Im Auftrag von' selector when there are no active delegations", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));

    expect(screen.queryByLabelText("Im Auftrag von")).not.toBeInTheDocument();
  });

  it("shows an 'Im Auftrag von' selector populated from active delegations", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    listActiveDelegationsForDeputyMock.mockResolvedValue([
      {
        id: "d1",
        delegator_principal_id: "carol-sub",
        deputy_principal_id: "u1",
        starts_at: "2026-01-01T00:00:00Z",
        ends_at: "2026-12-31T00:00:00Z",
      },
    ]);
    renderList();

    await waitFor(() =>
      expect(listActiveDelegationsForDeputyMock).toHaveBeenCalledWith("token-123", "u1")
    );
    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));

    expect(screen.getByLabelText("Im Auftrag von")).toBeInTheDocument();
    expect(screen.getByText("Für mich selbst")).toBeInTheDocument();
    expect(screen.getByText("carol-sub")).toBeInTheDocument();
  });

  it("completes a task on behalf of a delegator when selected", async () => {
    listReadyTasksMock.mockResolvedValueOnce([MANUAL_TASK]).mockResolvedValueOnce([]);
    listActiveDelegationsForDeputyMock.mockResolvedValue([
      {
        id: "d1",
        delegator_principal_id: "carol-sub",
        deputy_principal_id: "u1",
        starts_at: "2026-01-01T00:00:00Z",
        ends_at: "2026-12-31T00:00:00Z",
      },
    ]);
    completeTaskMock.mockResolvedValue(undefined);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    await waitFor(() => expect(screen.getByLabelText("Im Auftrag von")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Im Auftrag von"), {
      target: { value: "carol-sub" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Abschließen" }));

    await waitFor(() =>
      expect(completeTaskMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
        completedBy: "alice",
        data: {},
        signatureId: undefined,
        onBehalfOfPrincipalId: "carol-sub",
      })
    );
  });

  it("claims an unclaimed task as the logged-in user and reloads", async () => {
    listReadyTasksMock
      .mockResolvedValueOnce([MANUAL_TASK])
      .mockResolvedValueOnce([{ ...MANUAL_TASK, claimed_by: "alice" }]);
    claimTaskMock.mockResolvedValue(undefined);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Beanspruchen" }));

    await waitFor(() =>
      expect(claimTaskMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
        principalId: "alice",
      })
    );
    expect(await screen.findByText("Beansprucht von alice")).toBeInTheDocument();
  });

  it("shows a claim by someone else without offering a release button", async () => {
    listReadyTasksMock.mockResolvedValue([{ ...MANUAL_TASK, claimed_by: "bob" }]);
    renderList();

    expect(await screen.findByText("Beansprucht von bob")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Freigeben" })).not.toBeInTheDocument();
  });

  it("releases a task claim held by the logged-in user", async () => {
    listReadyTasksMock
      .mockResolvedValueOnce([{ ...MANUAL_TASK, claimed_by: "alice" }])
      .mockResolvedValueOnce([MANUAL_TASK]);
    releaseTaskClaimMock.mockResolvedValue(undefined);
    renderList();

    await waitFor(() => expect(screen.getByText("Beansprucht von alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Freigeben" }));

    await waitFor(() =>
      expect(releaseTaskClaimMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
      })
    );
    expect(await screen.findByText("Beanspruchen")).toBeInTheDocument();
  });

  it("offers the org-hierarchy grant form only once claimed by the logged-in user, and reports the result", async () => {
    listReadyTasksMock.mockResolvedValue([{ ...MANUAL_TASK, claimed_by: "alice" }]);
    createTaskOrgHierarchyGrantMock.mockResolvedValue({
      grant_kind: "supervisor",
      deputy_principal_ids: ["petra-supervisor"],
    });
    renderList();

    await waitFor(() => expect(screen.getByText("Beansprucht von alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    const form = await screen.findByRole("form", { name: "Zugriffsfreigabe erteilen" });
    fireEvent.click(within(form).getByRole("button", { name: "Freigabe erteilen" }));

    await waitFor(() =>
      expect(createTaskOrgHierarchyGrantMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
        grantKind: "supervisor",
        orgUnitOf: undefined,
      })
    );
    expect(
      await screen.findByText("Freigabe erteilt an: petra-supervisor")
    ).toBeInTheDocument();
  });

  it("does not offer the org-hierarchy grant form for an unclaimed task", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));

    expect(
      screen.queryByRole("form", { name: "Zugriffsfreigabe erteilen" })
    ).not.toBeInTheDocument();
  });

  it("sends org_unit_of only for grant_kind=org_unit", async () => {
    listReadyTasksMock.mockResolvedValue([{ ...MANUAL_TASK, claimed_by: "alice" }]);
    createTaskOrgHierarchyGrantMock.mockResolvedValue({
      grant_kind: "org_unit",
      deputy_principal_ids: [],
    });
    renderList();

    await waitFor(() => expect(screen.getByText("Beansprucht von alice")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    const form = await screen.findByRole("form", { name: "Zugriffsfreigabe erteilen" });
    fireEvent.change(within(form).getByLabelText("Freigabe für"), {
      target: { value: "org_unit" },
    });
    fireEvent.change(within(form).getByLabelText("Organisationseinheit von"), {
      target: { value: "creator" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Freigabe erteilen" }));

    await waitFor(() =>
      expect(createTaskOrgHierarchyGrantMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
        grantKind: "org_unit",
        orgUnitOf: "creator",
      })
    );
    expect(
      await screen.findByText(
        "Keine Person gefunden (keine Vorgesetzten/Gruppenmitgliedschaft konfiguriert)."
      )
    ).toBeInTheDocument();
  });
});
