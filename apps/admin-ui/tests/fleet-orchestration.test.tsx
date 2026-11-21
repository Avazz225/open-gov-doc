import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FleetOrchestration } from "@/components/FleetOrchestration";
import { I18nProvider } from "@/i18n";
import type { FleetOperator, FleetRollout } from "@/lib/api";

// P88-S1: groups, update plans, rollouts and operators in admin-ui.

const mocks = vi.hoisted(() => ({
  listFleetGroups: vi.fn(),
  listFleetPlans: vi.fn(),
  listFleetRollouts: vi.fn(),
  listFleetOperators: vi.fn(),
  createFleetGroup: vi.fn(),
  deleteFleetGroup: vi.fn(),
  addFleetGroupMember: vi.fn(),
  removeFleetGroupMember: vi.fn(),
  createFleetPlan: vi.fn(),
  deleteFleetPlan: vi.fn(),
  createFleetRollout: vi.fn(),
  deleteFleetRollout: vi.fn(),
  startFleetRollout: vi.fn(),
  cancelFleetRollout: vi.fn(),
  fleetRunAction: vi.fn(),
  createFleetOperator: vi.fn(),
  revokeFleetOperator: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...Object.fromEntries(
    Object.keys(mocks).map((name) => [
      name,
      (...args: unknown[]) => (mocks as Record<string, (...a: unknown[]) => unknown>)[name](...args),
    ])
  ),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

const KEY = "op-token";
const ME: FleetOperator = { id: "op-a", name: "anna", is_admin: true, created_at: "2026-01-01T00:00:00Z", revoked_at: null };
const INSTALLATIONS = [
  { id: "kiel", display_name: "Kiel", gateway_base_url: "https://kiel", created_at: "", updated_at: "" },
  { id: "bonn", display_name: "Bonn", gateway_base_url: "https://bonn", created_at: "", updated_at: "" },
  { id: "jena", display_name: "Jena", gateway_base_url: "https://jena", created_at: "", updated_at: "" },
];
const PLAN = {
  id: "plan-1",
  name: "Standard-Update",
  version: "2.0",
  created_at: "2026-01-01T00:00:00Z",
  steps: [
    { name: "Wartungsmodus", step_type: "gate", requires_approval: false },
    { name: "Verifikation", step_type: "verify", requires_approval: false },
    { name: "Freigabe", step_type: "gate", requires_approval: true },
  ],
};

function run(overrides: Partial<FleetRollout["runs"][number]> = {}) {
  return {
    id: "run-1",
    installation_id: "kiel",
    installation_display_name: "Kiel",
    current_step_index: 0,
    current_step_name: "Wartungsmodus",
    status: "wait_external" as const,
    last_outcome: null,
    error_message: null,
    proposed_by: null,
    started_at: null,
    updated_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

function rollout(overrides: Partial<FleetRollout> = {}): FleetRollout {
  return {
    id: "ro-1",
    plan_id: "plan-1",
    name: "Welle 1",
    group_id: null,
    status: "running",
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    started_by: "anna",
    runs: [run()],
    ...overrides,
  };
}

function renderView(operator: FleetOperator = ME) {
  return render(
    <I18nProvider>
      <FleetOrchestration operatorKey={KEY} operator={operator} installations={INSTALLATIONS} />
    </I18nProvider>
  );
}

function section(name: string) {
  return screen.getByRole("region", { name });
}

describe("FleetOrchestration", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockReset().mockResolvedValue(undefined));
    mocks.listFleetGroups.mockResolvedValue([
      { id: "g1", name: "Welle Nord", created_at: "", installation_ids: ["kiel", "bonn"] },
    ]);
    mocks.listFleetPlans.mockResolvedValue([PLAN]);
    mocks.listFleetRollouts.mockResolvedValue([]);
    mocks.listFleetOperators.mockResolvedValue([ME, { ...ME, id: "boot", name: "bootstrap" }]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("lists groups with member names and adds and removes members", async () => {
    renderView();
    const groups = await screen.findByRole("region", { name: "Gruppen (Wellen)" });
    expect(await within(groups).findByText("Welle Nord")).toBeInTheDocument();
    expect(within(groups).getByText("Kiel")).toBeInTheDocument();

    fireEvent.click(within(groups).getByRole("button", { name: "Kiel aus Welle Nord entfernen" }));
    await waitFor(() => expect(mocks.removeFleetGroupMember).toHaveBeenCalledWith("g1", "kiel", KEY));

    // Only installations not yet in the group are offered.
    const select = within(groups).getByRole("combobox", { name: "Installation zu Welle Nord hinzufügen" });
    expect(within(select).queryByRole("option", { name: "Kiel" })).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: "jena" } });
    fireEvent.click(within(groups).getByRole("button", { name: "Hinzufügen" }));
    await waitFor(() => expect(mocks.addFleetGroupMember).toHaveBeenCalledWith("g1", "jena", KEY));
  });

  it("shows a server conflict when a group can't be deleted", async () => {
    const { ApiError } = await import("@/lib/api");
    mocks.deleteFleetGroup.mockRejectedValue(new ApiError(409, "Gruppe wird von einem Rollout verwendet"));
    renderView();
    const groups = await screen.findByRole("region", { name: "Gruppen (Wellen)" });
    await within(groups).findByText("Welle Nord");

    fireEvent.click(within(groups).getByRole("button", { name: "Entfernen" }));

    expect(await within(groups).findByRole("alert")).toHaveTextContent("Gruppe wird von einem Rollout verwendet");
  });

  it("creates a plan with ordered steps and drops approval from check steps", async () => {
    renderView();
    const form = await screen.findByRole("form", { name: "Neuer Update-Plan" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Hotfix" } });
    fireEvent.change(within(form).getByLabelText("Version"), { target: { value: "2.1" } });
    fireEvent.change(within(form).getByLabelText("Schritt"), { target: { value: "Prüfen" } });
    fireEvent.click(within(form).getByLabelText("mit Freigabe"));
    fireEvent.change(within(form).getByLabelText("Art"), { target: { value: "verify" } });
    fireEvent.click(within(form).getByRole("button", { name: "Schritt hinzufügen" }));
    fireEvent.change(within(form).getAllByLabelText("Schritt")[1], { target: { value: "Update" } });
    // Move "Update" in front of "Prüfen".
    fireEvent.click(within(form).getByRole("button", { name: "Schritt 2 nach oben" }));
    fireEvent.click(within(form).getByRole("button", { name: "Plan anlegen" }));

    await waitFor(() =>
      expect(mocks.createFleetPlan).toHaveBeenCalledWith(
        {
          name: "Hotfix",
          version: "2.1",
          steps: [
            { name: "Update", step_type: "gate", requires_approval: false },
            { name: "Prüfen", step_type: "verify", requires_approval: false },
          ],
        },
        KEY
      )
    );
  });

  it("refuses a plan with an unnamed step without calling the server", async () => {
    renderView();
    const form = await screen.findByRole("form", { name: "Neuer Update-Plan" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Hotfix" } });
    fireEvent.change(within(form).getByLabelText("Version"), { target: { value: "2.1" } });
    fireEvent.click(within(form).getByRole("button", { name: "Plan anlegen" }));

    expect(await screen.findByText("Jeder Schritt braucht einen Namen.")).toBeInTheDocument();
    expect(mocks.createFleetPlan).not.toHaveBeenCalled();
  });

  it("creates a rollout from a group minus an excluded plus an extra installation", async () => {
    renderView();
    const form = await screen.findByRole("form", { name: "Neuer Rollout" });
    await within(form).findByRole("option", { name: "Welle Nord" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Welle 1" } });
    fireEvent.change(within(form).getByLabelText("Plan"), { target: { value: "plan-1" } });
    fireEvent.change(within(form).getByLabelText("Gruppe"), { target: { value: "g1" } });
    expect(within(form).getByText("Zielkreis: 2 Installation(en)")).toBeInTheDocument();

    fireEvent.click(within(form).getByRole("checkbox", { name: "Bonn" })); // exclude
    fireEvent.click(within(form).getByRole("checkbox", { name: "Jena" })); // include
    expect(within(form).getByText("Zielkreis: 2 Installation(en)")).toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "Rollout anlegen" }));

    await waitFor(() =>
      expect(mocks.createFleetRollout).toHaveBeenCalledWith(
        { plan_id: "plan-1", name: "Welle 1", group_id: "g1", include: ["jena"], exclude: ["bonn"] },
        KEY
      )
    );
  });

  it("starts a draft rollout and offers deleting it", async () => {
    mocks.listFleetRollouts.mockResolvedValue([rollout({ status: "draft", started_by: null, runs: [run({ status: "pending" })] })]);
    renderView();
    const item = await screen.findByRole("listitem", { name: "Welle 1" });
    expect(within(item).getByText("Entwurf")).toBeInTheDocument();
    expect(within(item).getByText("Nicht gestartet")).toBeInTheDocument();
    expect(within(item).getByRole("button", { name: "Entfernen" })).toBeInTheDocument();

    fireEvent.click(within(item).getByRole("button", { name: "Starten" }));
    await waitFor(() => expect(mocks.startFleetRollout).toHaveBeenCalledWith("ro-1", KEY));
  });

  it("cancels a stuck running rollout, which can then be deleted", async () => {
    mocks.listFleetRollouts.mockResolvedValueOnce([rollout()]).mockResolvedValue([rollout({ status: "cancelled" })]);
    renderView();
    let item = await screen.findByRole("listitem", { name: "Welle 1" });
    fireEvent.click(within(item).getByRole("button", { name: "Abbrechen" }));
    await waitFor(() => expect(mocks.cancelFleetRollout).toHaveBeenCalledWith("ro-1", KEY));

    item = await screen.findByRole("listitem", { name: "Welle 1" });
    expect(await within(item).findByText("Abgebrochen")).toBeInTheDocument();
    // Its runs are frozen: no step actions, but it can go.
    expect(within(item).queryByRole("button", { name: "Ergebnis melden" })).not.toBeInTheDocument();
    expect(within(item).getByRole("button", { name: "Entfernen" })).toBeInTheDocument();
  });

  it("reports a gate step with an outcome and a note", async () => {
    mocks.listFleetRollouts.mockResolvedValue([rollout()]);
    renderView();
    const item = await screen.findByRole("listitem", { name: "Welle 1" });
    expect(within(item).getByText(/1\/3:/)).toBeInTheDocument();
    // A running rollout can't be deleted.
    expect(within(item).queryByRole("button", { name: "Entfernen" })).not.toBeInTheDocument();

    fireEvent.click(within(item).getByRole("button", { name: "Ergebnis melden" }));
    const form = within(item).getByRole("form", { name: "Ergebnis für Kiel melden" });
    fireEvent.change(within(form).getByLabelText("Ergebnis"), { target: { value: "retry_later" } });
    fireEvent.change(within(form).getByLabelText("Hinweis (optional)"), { target: { value: "Wartungsfenster verschoben" } });
    fireEvent.click(within(form).getByRole("button", { name: "Melden" }));

    await waitFor(() =>
      expect(mocks.fleetRunAction).toHaveBeenCalledWith("ro-1", "kiel", "mark-done", KEY, {
        outcome: "retry_later",
        detail: "Wartungsfenster verschoben",
      })
    );
  });

  it("checks a verify step", async () => {
    mocks.listFleetRollouts.mockResolvedValue([
      rollout({ runs: [run({ current_step_index: 1, current_step_name: "Verifikation", status: "retry_later", error_message: "timeout" })] }),
    ]);
    renderView();
    const item = await screen.findByRole("listitem", { name: "Welle 1" });
    expect(within(item).getByText("timeout")).toBeInTheDocument();

    fireEvent.click(within(item).getByRole("button", { name: "Prüfen" }));
    await waitFor(() => expect(mocks.fleetRunAction).toHaveBeenCalledWith("ro-1", "kiel", "advance", KEY, undefined));
  });

  it("does not let the proposer approve their own proposal, but a second operator can", async () => {
    const proposed = rollout({
      runs: [run({ current_step_index: 2, current_step_name: "Freigabe", status: "manual_required", proposed_by: "anna" })],
    });
    mocks.listFleetRollouts.mockResolvedValue([proposed]);
    const { unmount } = renderView();
    let item = await screen.findByRole("listitem", { name: "Welle 1" });
    expect(within(item).getByRole("button", { name: "Freigeben" })).toBeDisabled();
    expect(within(item).getAllByText("Eigener Vorschlag: die Freigabe braucht einen anderen Operator.").length).toBeGreaterThan(0);
    unmount();

    renderView({ ...ME, id: "op-b", name: "bernd", is_admin: false });
    item = await screen.findByRole("listitem", { name: "Welle 1" });
    expect(within(item).getByText("Vorgeschlagen von anna")).toBeInTheDocument();
    fireEvent.click(within(item).getByRole("button", { name: "Freigeben" }));
    await waitFor(() => expect(mocks.fleetRunAction).toHaveBeenCalledWith("ro-1", "kiel", "approve", KEY, undefined));
  });

  it("rejects a proposal with a reason", async () => {
    mocks.listFleetRollouts.mockResolvedValue([
      rollout({ runs: [run({ current_step_index: 2, current_step_name: "Freigabe", status: "manual_required", proposed_by: "bernd" })] }),
    ]);
    renderView();
    const item = await screen.findByRole("listitem", { name: "Welle 1" });
    fireEvent.click(within(item).getByRole("button", { name: "Ablehnen" }));
    const form = within(item).getByRole("form", { name: "Freigabe für Kiel ablehnen" });
    fireEvent.change(within(form).getByLabelText("Grund"), { target: { value: "Backup fehlt" } });
    fireEvent.click(within(form).getByRole("button", { name: "Ablehnen" }));

    await waitFor(() =>
      expect(mocks.fleetRunAction).toHaveBeenCalledWith("ro-1", "kiel", "reject", KEY, { reason: "Backup fehlt" })
    );
  });

  it("offers retry for a failed step and an explicit confirmation for a fatal one", async () => {
    mocks.listFleetRollouts.mockResolvedValue([
      rollout({
        runs: [
          run({ status: "recoverable_failed" }),
          run({ id: "run-2", installation_id: "bonn", installation_display_name: "Bonn", status: "fatal_contract" }),
        ],
      }),
    ]);
    renderView();
    const item = await screen.findByRole("listitem", { name: "Welle 1" });
    fireEvent.click(within(item).getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(mocks.fleetRunAction).toHaveBeenCalledWith("ro-1", "kiel", "retry", KEY, undefined));

    fireEvent.click(within(item).getByRole("button", { name: "Korrektur bestätigen" }));
    await waitFor(() =>
      expect(mocks.fleetRunAction).toHaveBeenCalledWith("ro-1", "bonn", "acknowledge-fatal", KEY, undefined)
    );
  });

  it("mints an operator and shows the token once; bootstrap can't be revoked here", async () => {
    mocks.createFleetOperator.mockResolvedValue({ ...ME, id: "op-c", name: "carla", is_admin: false, token: "tok-123" });
    renderView();
    const operators = section("Operatoren");
    await within(operators).findByText("bootstrap");
    // Only one revoke button: for anna, not for bootstrap.
    expect(within(operators).getAllByRole("button", { name: "Widerrufen" })).toHaveLength(1);

    fireEvent.change(within(operators).getByLabelText("Name"), { target: { value: "carla" } });
    fireEvent.click(within(operators).getByRole("button", { name: "Operator anlegen" }));

    expect(await within(operators).findByText("tok-123")).toBeInTheDocument();
    expect(mocks.createFleetOperator).toHaveBeenCalledWith("carla", false, KEY);
  });

  it("hides operator management from a non-admin operator", async () => {
    renderView({ ...ME, is_admin: false });
    const operators = section("Operatoren");
    await within(operators).findByText("bootstrap");
    expect(within(operators).queryByRole("button", { name: "Operator anlegen" })).not.toBeInTheDocument();
    expect(within(operators).queryByRole("button", { name: "Widerrufen" })).not.toBeInTheDocument();
  });
});
