import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LogLevelSettings } from "@/components/LogLevelSettings";
import { I18nProvider } from "@/i18n";

// P97-S2 (ADR 0267): central log levels on the monitoring page.

const mocks = vi.hoisted(() => ({
  getLogLevels: vi.fn(),
  setLogLevel: vi.fn(),
  resetLogLevel: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getLogLevels: (...a: unknown[]) => mocks.getLogLevels(...a),
  setLogLevel: (...a: unknown[]) => mocks.setLogLevel(...a),
  resetLogLevel: (...a: unknown[]) => mocks.resetLogLevel(...a),
  lookupUserById: async () => ({ id: "u-alice", username: "alice", display_name: "Alice" }),
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
      permissions: ["admin.monitoring"],
      accessToken: "tok",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const SOON = new Date(Date.now() + 30 * 60000).toISOString();

const VIEW = {
  settings: [
    {
      scope: "logger:workflow-service:workflow_service.repository",
      service: "workflow-service",
      logger: "workflow_service.repository",
      level: "DEBUG",
      expires_at: SOON,
      updated_by: "u-alice",
      updated_at: "2026-11-26T19:00:00Z",
    },
    {
      scope: "global",
      service: null,
      logger: null,
      level: "WARNING",
      expires_at: null,
      updated_by: "u-alice",
      updated_at: "2026-11-26T19:00:00Z",
    },
  ],
  effective: {
    "workflow-service": {
      root_level: "WARNING",
      root_source: "global",
      loggers: { "workflow_service.repository": { level: "DEBUG", source: "service" } },
    },
    "permission-service": { root_level: "WARNING", root_source: "global", loggers: {} },
  },
  catalog: {
    "workflow-service": ["workflow_service.main", "workflow_service.repository"],
    "permission-service": ["permission_service.repository"],
  },
  debug_default_minutes: 60,
  debug_max_minutes: 1440,
};

function renderSection(canManage = true) {
  return render(
    <I18nProvider>
      <LogLevelSettings canManage={canManage} />
    </I18nProvider>
  );
}

describe("LogLevelSettings", () => {
  beforeEach(() => {
    mocks.getLogLevels.mockReset().mockResolvedValue(VIEW);
    mocks.setLogLevel.mockReset().mockResolvedValue(VIEW);
    mocks.resetLogLevel.mockReset().mockResolvedValue({ ...VIEW, settings: [] });
  });

  it("lists the settings with scope, level and expiry", async () => {
    renderSection();
    const row = (await screen.findByText("workflow-service › workflow_service.repository")).closest(
      "tr"
    )!;
    expect(within(row).getByText("DEBUG")).toBeInTheDocument();
    expect(within(row).getByText(/in (29|30) min/)).toBeInTheDocument();
    // "Alle Dienste" is also an option of the service picker; take the row.
    const globalRow = screen
      .getAllByText("Alle Dienste")
      .map((element) => element.closest("tr"))
      .find(Boolean)!;
    expect(within(globalRow).getByText("ohne Ablauf")).toBeInTheDocument();
  });

  it("sets a logger level with a duration and offers the service's loggers", async () => {
    renderSection();
    await screen.findByText("workflow-service › workflow_service.repository");

    fireEvent.change(screen.getByLabelText("Dienst"), { target: { value: "workflow-service" } });
    const options = Array.from(document.querySelectorAll("#log-level-logger-options option")).map(
      (o) => (o as HTMLOptionElement).value
    );
    expect(options).toEqual(["workflow_service.main", "workflow_service.repository"]);
    fireEvent.change(screen.getByLabelText("Logger (optional)"), {
      target: { value: "workflow_service.main" },
    });
    fireEvent.change(screen.getByLabelText("Dauer"), { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: "Setzen" }));

    await waitFor(() =>
      expect(mocks.setLogLevel).toHaveBeenCalledWith("tok", {
        service: "workflow-service",
        logger: "workflow_service.main",
        level: "DEBUG",
        expires_in_minutes: 15,
      })
    );
    expect(await screen.findByRole("status")).toHaveTextContent("15 Sekunden");
  });

  it("doesn't offer DEBUG without an expiry", async () => {
    renderSection();
    await screen.findByText("workflow-service › workflow_service.repository");
    const noExpiry = within(screen.getByLabelText("Dauer")).getByRole("option", {
      name: "ohne Ablauf",
    }) as HTMLOptionElement;
    expect(noExpiry.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Level"), { target: { value: "INFO" } });
    expect(noExpiry.disabled).toBe(false);
  });

  it("removes a setting", async () => {
    renderSection();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Einstellung workflow-service › workflow_service.repository entfernen",
      })
    );
    await waitFor(() =>
      expect(mocks.resetLogLevel).toHaveBeenCalledWith(
        "tok",
        "workflow-service",
        "workflow_service.repository"
      )
    );
    expect(await screen.findByText(/Keine zentralen Einstellungen/)).toBeInTheDocument();
  });

  it("is read-only without the monitoring role", async () => {
    renderSection(false);
    await screen.findByText("workflow-service › workflow_service.repository");
    expect(screen.queryByRole("button", { name: "Setzen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /entfernen/ })).not.toBeInTheDocument();
  });
});
