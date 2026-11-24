import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SensorSettings } from "@/components/SensorSettings";
import { I18nProvider } from "@/i18n";

// P88-S2 (ADR 0243): monitoring sensors page.

const mocks = vi.hoisted(() => ({
  listSensors: vi.fn(),
  getSensorConfig: vi.fn(),
  setSensorGlobalDefault: vi.fn(),
  setSensorOverride: vi.fn(),
  getTracingConfig: vi.fn(),
  setTracingSamplingRatio: vi.fn(),
  permissions: [] as string[],
}));

vi.mock("@/lib/api", () => ({
  listSensors: (...a: unknown[]) => mocks.listSensors(...a),
  getSensorConfig: (...a: unknown[]) => mocks.getSensorConfig(...a),
  setSensorGlobalDefault: (...a: unknown[]) => mocks.setSensorGlobalDefault(...a),
  setSensorOverride: (...a: unknown[]) => mocks.setSensorOverride(...a),
  getTracingConfig: (...a: unknown[]) => mocks.getTracingConfig(...a),
  setTracingSamplingRatio: (...a: unknown[]) => mocks.setTracingSamplingRatio(...a),
  // P88-S6: alerting has its own test file; here nothing is configured.
  listAlertRules: async () => [],
  listDowntimeWindows: async () => [],
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
      permissions: mocks.permissions,
      accessToken: "tok",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const SENSORS = [
  {
    name: "document.upload.duration",
    group: "performance",
    cost: 4,
    default_enabled: true,
    description: "Dauer eines Uploads",
    service_types: ["document-service"],
    active: false,
    source: "cost",
  },
  {
    name: "document.count.active_total",
    group: "capacity",
    cost: 2,
    default_enabled: true,
    description: "Aktive Dokumente",
    service_types: ["document-service"],
    active: true,
    source: "global",
  },
  {
    name: "storage.replication.backlog",
    group: "reliability",
    cost: 2,
    default_enabled: true,
    description: "Replikations-Rückstand",
    service_types: ["storage-service"],
    active: true,
    source: "sensor",
  },
];

function renderPage() {
  return render(
    <I18nProvider>
      <SensorSettings />
    </I18nProvider>
  );
}

describe("SensorSettings", () => {
  beforeEach(() => {
    mocks.permissions = ["admin.monitoring"];
    mocks.listSensors.mockReset().mockResolvedValue(SENSORS);
    mocks.getSensorConfig.mockReset().mockResolvedValue({
      global_default: true,
      overrides: { "cost:4": false, "storage.replication.backlog": true, "old.sensor": false },
    });
    mocks.setSensorGlobalDefault.mockReset().mockResolvedValue({});
    mocks.setSensorOverride.mockReset().mockResolvedValue({});
    mocks.getTracingConfig.mockReset().mockResolvedValue({
      sampling_ratio: 1,
      updated_by: null,
      updated_at: null,
      tracing_enabled: true,
    });
    mocks.setTracingSamplingRatio.mockReset();
  });

  it("sets the tracing sampling ratio in per cent (P94-S1)", async () => {
    mocks.setTracingSamplingRatio.mockResolvedValue({
      sampling_ratio: 0.05,
      updated_by: "admin",
      updated_at: "2026-11-24T10:00:00Z",
      tracing_enabled: true,
    });
    renderPage();
    const tracing = await screen.findByRole("region", { name: "Tracing" });
    const input = await within(tracing).findByLabelText("Aufgezeichneter Anteil neuer Anfragen");
    await waitFor(() => expect(input).toHaveValue("100"));

    fireEvent.change(input, { target: { value: "150" } });
    expect(within(tracing).getByText(/zwischen 0 und 100/)).toBeInTheDocument();
    expect(within(tracing).getByRole("button", { name: "Übernehmen" })).toBeDisabled();

    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(within(tracing).getByRole("button", { name: "Übernehmen" }));
    expect(await within(tracing).findByRole("status")).toHaveTextContent("Gespeichert");
    expect(mocks.setTracingSamplingRatio).toHaveBeenCalledWith("tok", 0.05);
    expect(within(tracing).getByText(/Zuletzt geändert von admin/)).toBeInTheDocument();
  });

  it("shows the ratio read-only and says when no collector is configured", async () => {
    mocks.permissions = [];
    mocks.getTracingConfig.mockResolvedValue({
      sampling_ratio: 0.1,
      updated_by: null,
      updated_at: null,
      tracing_enabled: false,
    });
    renderPage();
    const tracing = await screen.findByRole("region", { name: "Tracing" });
    expect(await within(tracing).findByText(/kein Trace-Collector/)).toBeInTheDocument();
    expect(within(tracing).getByLabelText("Aufgezeichneter Anteil neuer Anfragen")).toBeDisabled();
    expect(within(tracing).queryByRole("button", { name: "Übernehmen" })).toBeNull();
  });

  it("shows the catalog grouped, with cost, state and where the state comes from", async () => {
    renderPage();
    expect(await screen.findByText("document.upload.duration")).toBeInTheDocument();
    expect(screen.getByText("Performance")).toBeInTheDocument();
    expect(screen.getByText("über die Kosten")).toBeInTheDocument();
    expect(screen.getByText("eigene Einstellung")).toBeInTheDocument();
    expect(screen.getByText("2 von 3 aktiv")).toBeInTheDocument();
    expect(screen.getByLabelText("Einstellung für Kostenstufe 4")).toHaveValue("off");
    expect(screen.getByLabelText("Einstellung für storage.replication.backlog")).toHaveValue("on");
  });

  it("switches the base setting", async () => {
    renderPage();
    fireEvent.click(await screen.findByLabelText("Nichts überwachen"));
    await waitFor(() => expect(mocks.setSensorGlobalDefault).toHaveBeenCalledWith("tok", false));
  });

  it("sets a group, clears a cost-class override and overrides one sensor", async () => {
    renderPage();
    fireEvent.change(await screen.findByLabelText("Einstellung für die Gruppe Kapazität"), {
      target: { value: "off" },
    });
    await waitFor(() => expect(mocks.setSensorOverride).toHaveBeenCalledWith("tok", "group:capacity", false));

    fireEvent.change(screen.getByLabelText("Einstellung für Kostenstufe 4"), { target: { value: "inherit" } });
    await waitFor(() => expect(mocks.setSensorOverride).toHaveBeenCalledWith("tok", "cost:4", null));

    fireEvent.change(screen.getByLabelText("Einstellung für document.upload.duration"), {
      target: { value: "on" },
    });
    await waitFor(() =>
      expect(mocks.setSensorOverride).toHaveBeenCalledWith("tok", "document.upload.duration", true)
    );
  });

  it("collapses a sensor shared by many services to a count", async () => {
    mocks.listSensors.mockResolvedValue([
      { ...SENSORS[1], name: "http.requests", service_types: ["a-service", "b-service", "c-service", "d-service"] },
    ]);
    renderPage();
    expect(await screen.findByText("4 Dienste")).toBeInTheDocument();
  });

  it("shows the cost level as dots and words, and marks default-off sensors", async () => {
    mocks.listSensors.mockResolvedValue([
      { ...SENSORS[1], name: "document.count.by_type", cost: 5, default_enabled: false, active: false, source: "default" },
    ]);
    renderPage();
    const dots = await screen.findAllByRole("img", { name: "Kostenstufe 5 von 5: Sehr teuer" });
    // One in the cost section, one in the sensor's row.
    expect(dots).toHaveLength(2);
    expect(dots[1].querySelectorAll(".cost-dot.filled")).toHaveLength(5);
    expect(screen.getAllByText("standardmäßig aus").length).toBeGreaterThanOrEqual(2);
    // All five levels get a switch.
    for (const level of [1, 2, 3, 4, 5]) {
      expect(screen.getByRole("combobox", { name: `Einstellung für Kostenstufe ${level}` })).toBeInTheDocument();
    }
  });

  it("filters sensors by service", async () => {
    renderPage();
    await screen.findByText("document.upload.duration");
    fireEvent.change(screen.getByLabelText("Filtern nach Name, Beschreibung oder Dienst"), {
      target: { value: "storage" },
    });
    expect(screen.queryByText("document.upload.duration")).not.toBeInTheDocument();
    expect(screen.getByText("storage.replication.backlog")).toBeInTheDocument();
  });

  it("lists settings for sensors no service reports and resets them", async () => {
    renderPage();
    const orphaned = await screen.findByRole("region", { name: "Einstellungen ohne laufenden Sensor" });
    expect(within(orphaned).getByText("old.sensor")).toBeInTheDocument();
    // Cost-class keys are never orphaned.
    expect(within(orphaned).queryByText("cost:4")).not.toBeInTheDocument();
    fireEvent.click(within(orphaned).getByRole("button", { name: "Zurücksetzen" }));
    await waitFor(() => expect(mocks.setSensorOverride).toHaveBeenCalledWith("tok", "old.sensor", null));
  });

  it("is read-only without admin.monitoring", async () => {
    mocks.permissions = [];
    renderPage();
    await screen.findByText("document.upload.duration");
    expect(screen.getByText(/Nur lesend/)).toBeInTheDocument();
    expect(screen.getByLabelText("Einstellung für Kostenstufe 4")).toBeDisabled();
    expect(screen.getByLabelText("Nichts überwachen")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Zurücksetzen" })).not.toBeInTheDocument();
  });

  it("shows a server error", async () => {
    const { ApiError } = await import("@/lib/api");
    mocks.setSensorOverride.mockRejectedValue(new ApiError(403, "Fehlende Domain-Admin-Rolle"));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Einstellung für Kostenstufe 4"), { target: { value: "on" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Fehlende Domain-Admin-Rolle");
  });
});
