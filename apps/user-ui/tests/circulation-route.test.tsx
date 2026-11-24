import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CirculationRouteSection, NewCirculationSection } from "@/components/CirculationRoute";
import { I18nProvider } from "@/i18n";
import type { CirculationRoute, RouteStep } from "@/lib/api";

const api = vi.hoisted(() => ({
  getCirculationRoute: vi.fn(),
  addRouteStep: vi.fn(),
  removeRouteStep: vi.fn(),
  reorderRoute: vi.fn(),
  recallRouteStep: vi.fn(),
  createCase: vi.fn(),
  listProcessDefinitions: vi.fn(),
  lookupUserByUsername: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    ...Object.fromEntries(
      Object.keys(api).map((name) => [
        name,
        (...args: unknown[]) =>
          (api as Record<string, (...a: unknown[]) => unknown>)[name](...args),
      ])
    ),
  };
});

// Principal ids are shown by name; here the "name" is the id itself.
vi.mock("@/lib/usePrincipalNames", () => ({
  usePrincipalNames: () => ({ "id-alice": "Alice", "id-bob": "Bob", "id-carol": "Carol" }),
  principalName: (names: Record<string, string>, value: string | null) =>
    value ? (names[value] ?? value) : "",
}));

function step(id: string, assignee: string, status: RouteStep["status"], extra = {}): RouteStep {
  return {
    id,
    position: 0,
    kind: "mitzeichnen",
    assignee_type: "principal",
    assignee,
    note: null,
    status,
    added_by: "id-alice",
    added_at: "2026-11-24T08:00:00Z",
    closed_by: null,
    closed_at: null,
    close_reason: null,
    ...extra,
  };
}

const route: CirculationRoute = {
  instance_id: "inst-1",
  instance_status: "running",
  workflow_version: 7,
  created_by: "id-alice",
  can_edit: true,
  steps: [
    step("s1", "id-bob", "current"),
    step("s2", "id-carol", "pending"),
    step("s3", "Kasse", "pending", { assignee_type: "group", kind: "zeichnen" }),
  ],
};

function renderSection() {
  return render(
    <I18nProvider>
      <CirculationRouteSection token="t" instanceId="inst-1" />
    </I18nProvider>
  );
}

async function fillReason(text = "Haushalt betroffen") {
  fireEvent.change(await screen.findByLabelText("Begründung der Änderung"), {
    target: { value: text },
  });
}

describe("CirculationRouteSection", () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.getCirculationRoute.mockResolvedValue(route);
  });

  it("renders nothing for a case that isn't a circulation", async () => {
    api.getCirculationRoute.mockRejectedValue(new Error("404"));
    const { container } = renderSection();
    await waitFor(() => expect(api.getCirculationRoute).toHaveBeenCalled());
    expect(container.querySelector("section")).toBeNull();
  });

  it("shows the route with names and statuses", async () => {
    renderSection();
    const list = await screen.findByRole("list");
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("1. Mitzeichnen: Bob");
    expect(items[0]).toHaveTextContent("Liegt gerade hier");
    expect(items[2]).toHaveTextContent("3. Zeichnen: Gruppe Kasse");
  });

  it("adds a co-signer as the next step with the reason and version", async () => {
    api.lookupUserByUsername.mockResolvedValue({ id: "id-dora", username: "dora" });
    api.addRouteStep.mockResolvedValue(route);
    renderSection();
    await fillReason();
    const form = screen.getByRole("form", { name: "Schritt hinzufügen" });
    fireEvent.change(within(form).getByLabelText("Benutzername"), { target: { value: "dora" } });
    fireEvent.click(within(form).getByRole("button", { name: "Hinzufügen" }));

    await screen.findByText("Schritt hinzugefügt.");
    expect(api.addRouteStep).toHaveBeenCalledWith("t", "inst-1", {
      step: { kind: "mitzeichnen", assignee_type: "principal", assignee: "id-dora", note: null },
      index: 0,
      reason: "Haushalt betroffen",
      expectedVersion: 7,
    });
  });

  it("strikes and reorders only future steps, and needs a reason first", async () => {
    api.removeRouteStep.mockResolvedValue(route);
    api.reorderRoute.mockResolvedValue(route);
    renderSection();
    const strike = await screen.findByRole("button", { name: "Carol streichen" });
    expect(strike).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Bob streichen" })).toBeNull();

    await fillReason("im Urlaub");
    fireEvent.click(strike);
    await screen.findByText("Schritt gestrichen.");
    expect(api.removeRouteStep).toHaveBeenCalledWith("t", "inst-1", "s2", {
      reason: "im Urlaub",
      expectedVersion: 7,
    });

    await fillReason("Zeichnung vorziehen");
    fireEvent.click(screen.getByRole("button", { name: "Gruppe Kasse nach vorn" }));
    await screen.findByText("Reihenfolge geändert.");
    expect(api.reorderRoute).toHaveBeenCalledWith("t", "inst-1", {
      stepIds: ["s3", "s2"],
      reason: "Zeichnung vorziehen",
      expectedVersion: 7,
    });
  });

  it("recalls the current step back to the author", async () => {
    api.recallRouteStep.mockResolvedValue(route);
    renderSection();
    await fillReason("Anlage fehlt");
    fireEvent.click(screen.getByRole("button", { name: "Zurückholen …" }));
    const form = screen.getByRole("form", { name: "Aktuellen Schritt zurückholen und umleiten" });
    expect(within(form).getByLabelText("Umleiten an")).toHaveDisplayValue("Verfasser/in (Alice)");
    fireEvent.click(within(form).getByRole("button", { name: "Zurückholen und umleiten" }));

    await screen.findByText("Schritt zurückgeholt und umgeleitet.");
    expect(api.recallRouteStep).toHaveBeenCalledWith("t", "inst-1", {
      redirect: {
        kind: "bearbeiten",
        assignee_type: "principal",
        assignee: "id-alice",
        note: null,
      },
      reason: "Anlage fehlt",
      expectedVersion: 7,
    });
  });

  it("is read-only without edit rights", async () => {
    api.getCirculationRoute.mockResolvedValue({ ...route, can_edit: false });
    renderSection();
    await screen.findByRole("list");
    expect(screen.queryByLabelText("Begründung der Änderung")).toBeNull();
    expect(screen.queryByRole("button", { name: "Zurückholen …" })).toBeNull();
  });
});

describe("NewCirculationSection", () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.listProcessDefinitions.mockResolvedValue([
      { id: 4, name: "Freigabe", version: 2, is_circulation: false },
      { id: 9, name: "Umlauf", version: 1, is_circulation: true },
    ]);
  });

  it("starts a circulation with the planned route", async () => {
    api.lookupUserByUsername.mockResolvedValue({ id: "id-bob", username: "bob" });
    api.createCase.mockResolvedValue({ id: "case-1" });
    const onCaseCreated = vi.fn();
    render(
      <I18nProvider>
        <NewCirculationSection token="t" onCaseCreated={onCaseCreated} />
      </I18nProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: "Neue Umlaufmappe" }));
    const form = await screen.findByRole("form", { name: "Neue Umlaufmappe" });
    await waitFor(() => expect(within(form).getByLabelText("Ablauf")).toHaveValue("9"));
    fireEvent.change(within(form).getByLabelText("Bezeichnung"), {
      target: { value: "Beschaffung Drucker" },
    });
    const start = within(form).getByRole("button", { name: "Umlaufmappe starten" });
    expect(start).toBeDisabled(); // no step yet

    fireEvent.change(within(form).getByLabelText("Benutzername"), { target: { value: "bob" } });
    fireEvent.click(within(form).getByRole("button", { name: "Zum Laufweg hinzufügen" }));
    expect(await within(form).findByText("bob")).toBeInTheDocument();
    fireEvent.click(start);

    await waitFor(() => expect(onCaseCreated).toHaveBeenCalledWith("case-1"));
    expect(api.createCase).toHaveBeenCalledWith("t", {
      name: "Beschaffung Drucker",
      processDefinitionId: 9,
      route: [{ kind: "mitzeichnen", assignee_type: "principal", assignee: "id-bob", note: null }],
    });
  });
});
