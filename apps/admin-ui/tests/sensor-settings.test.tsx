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
  permissions: [] as string[],
}));

vi.mock("@/lib/api", () => ({
  listSensors: (...a: unknown[]) => mocks.listSensors(...a),
  getSensorConfig: (...a: unknown[]) => mocks.getSensorConfig(...a),
  setSensorGlobalDefault: (...a: unknown[]) => mocks.setSensorGlobalDefault(...a),
  setSensorOverride: (...a: unknown[]) => mocks.setSensorOverride(...a),
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
    cost: "expensive",
    description: "Dauer eines Uploads",
    service_types: ["document-service"],
    active: false,
    source: "cost",
  },
  {
    name: "document.count.active_total",
    group: "capacity",
    cost: "cheap",
    description: "Aktive Dokumente",
    service_types: ["document-service"],
    active: true,
    source: "global",
  },
  {
    name: "storage.replication.backlog",
    group: "reliability",
    cost: "cheap",
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
      overrides: { "cost:expensive": false, "storage.replication.backlog": true, "old.sensor": false },
    });
    mocks.setSensorGlobalDefault.mockReset().mockResolvedValue({});
    mocks.setSensorOverride.mockReset().mockResolvedValue({});
  });

  it("shows the catalog grouped, with cost, state and where the state comes from", async () => {
    renderPage();
    expect(await screen.findByText("document.upload.duration")).toBeInTheDocument();
    expect(screen.getByText("Performance")).toBeInTheDocument();
    expect(screen.getByText("über die Kosten")).toBeInTheDocument();
    expect(screen.getByText("eigene Einstellung")).toBeInTheDocument();
    expect(screen.getByText("2 von 3 aktiv")).toBeInTheDocument();
    expect(screen.getByLabelText("Teuer")).toHaveValue("off");
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

    fireEvent.change(screen.getByLabelText("Teuer"), { target: { value: "inherit" } });
    await waitFor(() => expect(mocks.setSensorOverride).toHaveBeenCalledWith("tok", "cost:expensive", null));

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
    expect(within(orphaned).queryByText("cost:expensive")).not.toBeInTheDocument();
    fireEvent.click(within(orphaned).getByRole("button", { name: "Zurücksetzen" }));
    await waitFor(() => expect(mocks.setSensorOverride).toHaveBeenCalledWith("tok", "old.sensor", null));
  });

  it("is read-only without admin.monitoring", async () => {
    mocks.permissions = [];
    renderPage();
    await screen.findByText("document.upload.duration");
    expect(screen.getByText(/Nur lesend/)).toBeInTheDocument();
    expect(screen.getByLabelText("Teuer")).toBeDisabled();
    expect(screen.getByLabelText("Nichts überwachen")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Zurücksetzen" })).not.toBeInTheDocument();
  });

  it("shows a server error", async () => {
    const { ApiError } = await import("@/lib/api");
    mocks.setSensorOverride.mockRejectedValue(new ApiError(403, "Fehlende Domain-Admin-Rolle"));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Teuer"), { target: { value: "on" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Fehlende Domain-Admin-Rolle");
  });
});
