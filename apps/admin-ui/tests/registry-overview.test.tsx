import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RegistryOverview } from "@/components/RegistryOverview";
import { I18nProvider } from "@/i18n";

function renderRegistryOverview() {
  return render(
    <I18nProvider>
      <RegistryOverview />
    </I18nProvider>
  );
}

const listServiceInstancesMock = vi.fn();
const listReplicasMock = vi.fn();
const setExpectedReplicasMock = vi.fn();
const resetExpectedReplicasMock = vi.fn();
const forgetServiceInstanceMock = vi.fn();
let permissions: string[] = [];

vi.mock("@/lib/api", () => ({
  listServiceInstances: (...args: unknown[]) => listServiceInstancesMock(...args),
  listReplicas: (...args: unknown[]) => listReplicasMock(...args),
  setExpectedReplicas: (...args: unknown[]) => setExpectedReplicasMock(...args),
  resetExpectedReplicas: (...args: unknown[]) => resetExpectedReplicasMock(...args),
  forgetServiceInstance: (...args: unknown[]) => forgetServiceInstanceMock(...args),
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
      permissions,
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const INSTANCES = [
  {
    instance_id: "ocr-service-live",
    service_type: "ocr-service",
    version: "0.1.0",
    address: "http://ocr-service:8000",
    healthy: true,
    status: "active",
    registered_at: "2026-01-01T00:00:00Z",
    last_heartbeat_at: "2026-01-01T00:00:00Z",
  },
  {
    instance_id: "ocr-service-dead",
    service_type: "ocr-service",
    version: "0.1.0",
    address: "http://ocr-service:8000",
    healthy: false,
    status: "active",
    registered_at: "2026-01-01T00:00:00Z",
    last_heartbeat_at: "2026-01-01T00:00:00Z",
  },
];

const TYPES = [
  {
    service_type: "ocr-service",
    expected_min: 2,
    expected_max: 3,
    source: "admin",
    reachable: 1,
    draining: 0,
    unreachable: 1,
    state: "under",
    since: "2026-01-01T00:00:00Z",
    pending_state: null,
  },
  {
    service_type: "storage-service",
    expected_min: null,
    expected_max: null,
    source: "none",
    reachable: 1,
    draining: 0,
    unreachable: 0,
    state: "unmonitored",
    since: null,
    pending_state: null,
  },
];

// P87-S3 (ADR 0241): one row per service type, expandable to its instances.
describe("RegistryOverview", () => {
  beforeEach(() => {
    permissions = [];
    listServiceInstancesMock.mockReset().mockResolvedValue(INSTANCES);
    listReplicasMock.mockReset().mockResolvedValue(TYPES);
    setExpectedReplicasMock.mockReset().mockResolvedValue({});
    resetExpectedReplicasMock.mockReset().mockResolvedValue(undefined);
    forgetServiceInstanceMock.mockReset().mockResolvedValue(undefined);
  });

  it("shows each type's counts, expected range, source and state", async () => {
    renderRegistryOverview();

    expect(await screen.findByText("ocr-service")).toBeInTheDocument();
    expect(screen.getByText("2–3")).toBeInTheDocument();
    expect(screen.getByText("(Admin)")).toBeInTheDocument();
    expect(screen.getByText("Zu wenige")).toBeInTheDocument();
    expect(screen.getByText("Nicht überwacht")).toBeInTheDocument();
    expect(screen.getByText(/1 nicht erreichbar/)).toBeInTheDocument();
    // Read-only without admin.monitoring.
    expect(screen.queryByRole("button", { name: "Erwartung" })).not.toBeInTheDocument();
  });

  it("expands a type to its instances with translated status labels", async () => {
    renderRegistryOverview();

    fireEvent.click(await screen.findByRole("button", { name: "ocr-service" }));

    expect(screen.getByText("ocr-service-live")).toBeInTheDocument();
    expect(screen.getByText("Erreichbar")).toBeInTheDocument();
    expect(screen.getByText("Nicht erreichbar")).toBeInTheDocument();
  });

  it("saves an expected range with admin.monitoring", async () => {
    permissions = ["admin.monitoring"];
    renderRegistryOverview();

    await screen.findByText("ocr-service");
    fireEvent.click(screen.getAllByRole("button", { name: "Erwartung" })[1]);
    const form = screen.getByRole("form", { name: "Erwartete Replikas für storage-service" });
    fireEvent.change(within(form).getByLabelText("Mindestens"), { target: { value: "2" } });
    fireEvent.change(within(form).getByLabelText("Höchstens"), { target: { value: "4" } });
    fireEvent.click(within(form).getByRole("button", { name: "Speichern" }));

    await waitFor(() =>
      expect(setExpectedReplicasMock).toHaveBeenCalledWith("token-123", "storage-service", {
        min_replicas: 2,
        max_replicas: 4,
      })
    );
  });

  it("rejects a maximum below the minimum without calling the server", async () => {
    permissions = ["admin.monitoring"];
    renderRegistryOverview();

    await screen.findByText("ocr-service");
    fireEvent.click(screen.getAllByRole("button", { name: "Erwartung" })[0]);
    const form = screen.getByRole("form", { name: "Erwartete Replikas für ocr-service" });
    fireEvent.change(within(form).getByLabelText("Mindestens"), { target: { value: "3" } });
    fireEvent.change(within(form).getByLabelText("Höchstens"), { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "Speichern" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(setExpectedReplicasMock).not.toHaveBeenCalled();
    // An admin override can be reset to the default.
    fireEvent.click(within(form).getByRole("button", { name: "Auf Standard zurücksetzen" }));
    await waitFor(() =>
      expect(resetExpectedReplicasMock).toHaveBeenCalledWith("token-123", "ocr-service")
    );
  });

  it("offers forgetting only for unreachable instances", async () => {
    permissions = ["admin.monitoring"];
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRegistryOverview();

    fireEvent.click(await screen.findByRole("button", { name: "ocr-service" }));
    const forgetButtons = screen.getAllByRole("button", { name: "Vergessen" });
    expect(forgetButtons).toHaveLength(1);
    fireEvent.click(forgetButtons[0]);

    await waitFor(() =>
      expect(forgetServiceInstanceMock).toHaveBeenCalledWith("token-123", "ocr-service-dead")
    );
  });

  it("shows an empty state when nothing is registered or expected", async () => {
    listReplicasMock.mockResolvedValue([]);
    listServiceInstancesMock.mockResolvedValue([]);

    renderRegistryOverview();

    expect(await screen.findByText("Keine Instanzen registriert.")).toBeInTheDocument();
  });
});
