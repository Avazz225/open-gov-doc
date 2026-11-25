import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowInstances } from "@/components/WorkflowInstances";
import { I18nProvider } from "@/i18n";

const api = {
  listWorkflowInstances: vi.fn(),
  listWorkflowProcessDefinitions: vi.fn(),
  getWorkflowInstance: vi.fn(),
  getWorkflowProcessDefinition: vi.fn(),
  listWorkflowInstanceTasks: vi.fn(),
  listWorkflowCompletedTasks: vi.fn(),
  skipWorkflowTask: vi.fn(),
  sendBackWorkflowInstance: vi.fn(),
  previewWorkflowMigration: vi.fn(),
  migrateWorkflowInstance: vi.fn(),
  terminateWorkflowInstance: vi.fn(),
};

vi.mock("@/lib/api", () => ({
  ...Object.fromEntries(
    Object.keys({
      listWorkflowInstances: 0,
      listWorkflowProcessDefinitions: 0,
      getWorkflowInstance: 0,
      getWorkflowProcessDefinition: 0,
      listWorkflowInstanceTasks: 0,
      listWorkflowCompletedTasks: 0,
      skipWorkflowTask: 0,
      sendBackWorkflowInstance: 0,
      previewWorkflowMigration: 0,
      migrateWorkflowInstance: 0,
      terminateWorkflowInstance: 0,
    }).map((name) => [
      name,
      (...args: unknown[]) => (api as Record<string, (...a: unknown[]) => unknown>)[name](...args),
    ])
  ),
  lookupUserByUsername: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/components/PrincipalName", () => ({
  PrincipalName: ({ value }: { value: string | null }) => <span>{value}</span>,
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
    permissions: ["workflow.instance_modify"],
    accessToken: "token-123",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

const instance = {
  id: "inst-1",
  process_definition_id: 1,
  business_key: "AZ-2026-17",
  status: "running",
  created_by: "carla",
  created_at: "2026-11-20T08:00:00Z",
  updated_at: "2026-11-21T09:30:00Z",
  completed_at: null,
  workflow_version: 4,
};

function renderView() {
  return render(
    <I18nProvider>
      <WorkflowInstances />
    </I18nProvider>
  );
}

async function openDetail() {
  renderView();
  fireEvent.click(await screen.findByRole("button", { name: "AZ-2026-17" }));
  return screen.findByRole("region", { name: "Instanzdetails" });
}

describe("WorkflowInstances", () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.listWorkflowInstances.mockResolvedValue([instance]);
    // The overview holds only the newest version per family; the instance's
    // v1 is resolved by id, the family history by name.
    api.listWorkflowProcessDefinitions.mockImplementation((_token: string, name?: string) =>
      Promise.resolve(
        name === "Bescheid"
          ? [
              { id: 2, name: "Bescheid", version: 2 },
              { id: 1, name: "Bescheid", version: 1 },
            ]
          : [
              { id: 2, name: "Bescheid", version: 2 },
              { id: 3, name: "Anderes", version: 5 },
            ]
      )
    );
    api.getWorkflowProcessDefinition.mockResolvedValue({ id: 1, name: "Bescheid", version: 1 });
    api.getWorkflowInstance.mockResolvedValue(instance);
    api.listWorkflowInstanceTasks.mockResolvedValue([
      {
        id: "t2",
        name: "Freigeben",
        lane: "Leitung",
        extensions: {},
        claimed_by: "frida",
      },
    ]);
    api.listWorkflowCompletedTasks.mockResolvedValue([
      {
        id: "t1",
        name: "Prüfen",
        lane: null,
        completed_at: "2026-11-21T09:00:00Z",
      },
    ]);
  });

  it("lists running instances with their process version", async () => {
    renderView();
    expect(await screen.findByRole("button", { name: "AZ-2026-17" })).toBeInTheDocument();
    expect(await screen.findByText("Bescheid v1")).toBeInTheDocument();
    expect(api.listWorkflowInstances).toHaveBeenCalledWith("token-123", {
      status: "running",
      businessKey: undefined,
    });
  });

  it("applies the case filter only on search", async () => {
    renderView();
    await screen.findByRole("button", { name: "AZ-2026-17" });
    fireEvent.change(screen.getByLabelText("Vorgang"), { target: { value: " AZ-2026-17 " } });
    expect(api.listWorkflowInstances).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Suchen" }));
    await waitFor(() =>
      expect(api.listWorkflowInstances).toHaveBeenLastCalledWith("token-123", {
        status: "running",
        businessKey: "AZ-2026-17",
      })
    );
  });

  it("requires a reason before skipping and reports the outcome", async () => {
    const detail = await openDetail();
    const skip = await within(detail).findByRole("button", {
      name: "Überspringen",
    });
    expect(skip).toBeDisabled();

    api.skipWorkflowTask.mockResolvedValue({
      instance: { ...instance, workflow_version: 5 },
      operation: "skip",
      target: "Freigeben",
      tasks_before: ["Freigeben"],
      tasks_after: ["Ablegen"],
      released_claims: ["frida"],
    });
    fireEvent.change(within(detail).getByLabelText("Begründung"), {
      target: { value: "Telefonisch geklärt" },
    });
    fireEvent.click(skip);

    const status = await within(detail).findByText(/„Freigeben“ übersprungen/);
    expect(status).toHaveTextContent("Jetzt offen: Ablegen.");
    expect(status).toHaveTextContent("1 Beanspruchung(en) aufgehoben");
    expect(api.skipWorkflowTask).toHaveBeenCalledWith(
      "token-123",
      "inst-1",
      "t2",
      "Telefonisch geklärt",
      4
    );
  });

  it("terminates only after a reason and a confirmation", async () => {
    const detail = await openDetail();
    // Wait until the detail has loaded its tasks, as the skip test does.
    await within(detail).findByRole("button", { name: "Überspringen" });
    const terminate = within(detail).getByRole("button", { name: "Instanz beenden" });
    expect(terminate).toBeDisabled();
    fireEvent.change(within(detail).getByLabelText("Begründung"), {
      target: { value: "Testrest aufräumen" },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    fireEvent.click(terminate);
    expect(api.terminateWorkflowInstance).not.toHaveBeenCalled();

    api.terminateWorkflowInstance.mockResolvedValue({
      instance: { ...instance, status: "terminated" },
      operation: "terminate",
      target: "beendet",
      tasks_before: ["Freigeben"],
      tasks_after: [],
      released_claims: [],
    });
    confirm.mockReturnValueOnce(true);
    fireEvent.click(terminate);

    expect(await within(detail).findByRole("status")).toHaveTextContent(
      "Instanz beendet. Die Instanz ist beendet."
    );
    expect(api.terminateWorkflowInstance).toHaveBeenCalledWith(
      "token-123",
      "inst-1",
      "Testrest aufräumen",
      4
    );
    confirm.mockRestore();
  });

  it("sends back to the chosen completed task", async () => {
    const detail = await openDetail();
    api.sendBackWorkflowInstance.mockResolvedValue({
      instance,
      operation: "send_back",
      target: "Prüfen",
      tasks_before: ["Freigeben"],
      tasks_after: ["Prüfen"],
      released_claims: [],
    });
    fireEvent.change(within(detail).getByLabelText("Begründung"), {
      target: { value: "Unterlagen fehlen" },
    });
    await waitFor(() => expect(within(detail).getByLabelText("An Aufgabe")).toHaveValue("t1"));
    fireEvent.click(within(detail).getByRole("button", { name: "Zurücksenden" }));

    expect(await within(detail).findByText(/Zurückgesendet an „Prüfen“/)).toBeInTheDocument();
    expect(api.sendBackWorkflowInstance).toHaveBeenCalledWith(
      "token-123",
      "inst-1",
      "t1",
      "Unterlagen fehlen",
      4
    );
  });

  it("offers only newer versions of the same process and migrates after a positive preview", async () => {
    const detail = await openDetail();
    const target = await within(detail).findByLabelText("Zielversion");
    expect(
      within(target)
        .getAllByRole("option")
        .map((o) => o.textContent)
    ).toEqual(["Bescheid v2"]);
    const migrate = within(detail).getByRole("button", { name: "Migrieren" });
    fireEvent.change(within(detail).getByLabelText("Begründung"), {
      target: { value: "Vier-Augen-Prinzip" },
    });
    expect(migrate).toBeDisabled();

    api.previewWorkflowMigration.mockResolvedValue({
      possible: true,
      reasons: [],
      removed: [],
      added: ["Gegenlesen"],
      changed: ["Ablegen"],
      active_tasks: [{ name: "Freigeben", state: "READY", aligned_to: "Freigeben" }],
    });
    fireEvent.click(within(detail).getByRole("button", { name: "Vorschau" }));
    const report = await within(detail).findByLabelText("Migrationsvorschau");
    expect(report).toHaveTextContent("Migration möglich");
    expect(report).toHaveTextContent("Gegenlesen");
    expect(migrate).toBeEnabled();

    api.migrateWorkflowInstance.mockResolvedValue({
      instance: { ...instance, process_definition_id: 2 },
      operation: "migrate",
      target: "Bescheid v2",
      tasks_before: ["Freigeben"],
      tasks_after: ["Freigeben"],
      released_claims: [],
    });
    fireEvent.click(migrate);
    expect(await within(detail).findByText(/Migriert auf Bescheid v2/)).toBeInTheDocument();
    expect(api.migrateWorkflowInstance).toHaveBeenCalledWith(
      "token-123",
      "inst-1",
      2,
      "Vier-Augen-Prinzip",
      4
    );
  });

  it("keeps migrate disabled and names the missing counterpart when impossible", async () => {
    const detail = await openDetail();
    fireEvent.change(within(detail).getByLabelText("Begründung"), {
      target: { value: "Prozess vereinfacht" },
    });
    api.previewWorkflowMigration.mockResolvedValue({
      possible: false,
      reasons: ["Aktive Aufgabe 'Freigeben' hat in der neuen Version keine Entsprechung"],
      removed: ["Freigeben"],
      added: [],
      changed: [],
      active_tasks: [{ name: "Freigeben", state: "READY", aligned_to: null }],
    });
    fireEvent.click(await within(detail).findByRole("button", { name: "Vorschau" }));
    const report = await within(detail).findByLabelText("Migrationsvorschau");
    expect(report).toHaveTextContent("Migration nicht möglich");
    expect(report).toHaveTextContent("keine Entsprechung");
    expect(within(detail).getByRole("button", { name: "Migrieren" })).toBeDisabled();
  });
});
