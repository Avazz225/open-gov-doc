import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ActiveAlerts,
  AlertRuleForm,
  DowntimeWindows,
  ruleProblem,
  thresholdsSummary,
  toWindowInput,
  worstLevel,
} from "@/components/SensorAlerts";
import { SensorSettings } from "@/components/SensorSettings";
import { I18nProvider } from "@/i18n";
import type { AlertRule } from "@/lib/api";

// P88-S6 (ADR 0245): the alerting UI on the sensors page.

const mocks = vi.hoisted(() => ({
  listSensors: vi.fn(),
  getSensorConfig: vi.fn(),
  setSensorGlobalDefault: vi.fn(),
  setSensorOverride: vi.fn(),
  listAlertRules: vi.fn(),
  listDowntimeWindows: vi.fn(),
  createAlertRule: vi.fn(),
  updateAlertRule: vi.fn(),
  deleteAlertRule: vi.fn(),
  previewAlertRule: vi.fn(),
  saveDowntimeWindow: vi.fn(),
  deleteDowntimeWindow: vi.fn(),
  // P94-S1: the tracing section on the same page; not under test here.
  getTracingConfig: vi.fn(),
  setTracingSamplingRatio: vi.fn(),
  // P97-S2: the log-level section on the same page; not under test here.
  getLogLevels: vi.fn(),
  setLogLevel: vi.fn(),
  resetLogLevel: vi.fn(),
  permissions: ["admin.monitoring"] as string[],
}));

vi.mock("@/lib/api", () => ({
  ...Object.fromEntries(
    Object.keys(mocks)
      .filter((k) => k !== "permissions")
      .map((name) => [
        name,
        (...a: unknown[]) => (mocks as unknown as Record<string, (...x: unknown[]) => unknown>)[name](...a),
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

const SENSOR = {
  name: "storage.target.used_percent",
  group: "capacity",
  cost: 1,
  description: "Belegung",
  service_types: ["storage-service"],
  active: true,
  default_enabled: true,
  source: "global",
};

function rule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: "r1",
    name: "Speicher voll",
    sensor: "storage.target.used_percent",
    label_filter: { target: "local" },
    aggregation: "max",
    min_error: null,
    min_warning: null,
    max_warning: 80,
    max_error: 95,
    warning_repeat: 1,
    error_repeat: 2,
    enabled: true,
    description: null,
    updated_at: "2026-11-21T15:00:00Z",
    updated_by: "2",
    level: "warning",
    value: 85.5,
    since: "2026-11-21T15:01:00Z",
    evaluated_at: "2026-11-21T15:02:00Z",
    notified_level: "warning",
    suppressed: false,
    ...overrides,
  };
}

function renderWith(node: React.ReactNode) {
  return render(<I18nProvider>{node}</I18nProvider>);
}

describe("alerting UI", () => {
  beforeEach(() => {
    mocks.permissions = ["admin.monitoring"];
    Object.entries(mocks).forEach(([k, m]) => k !== "permissions" && (m as ReturnType<typeof vi.fn>).mockReset());
    mocks.listSensors.mockResolvedValue([SENSOR]);
    mocks.getSensorConfig.mockResolvedValue({ global_default: true, overrides: {} });
    mocks.getTracingConfig.mockResolvedValue({
      sampling_ratio: 1,
      updated_by: null,
      updated_at: null,
      tracing_enabled: true,
    });
    mocks.listAlertRules.mockResolvedValue([rule()]);
    mocks.listDowntimeWindows.mockResolvedValue([]);
    mocks.getLogLevels.mockResolvedValue({
      settings: [],
      effective: {},
      catalog: {},
      debug_default_minutes: 60,
      debug_max_minutes: 1440,
    });
    mocks.createAlertRule.mockResolvedValue({});
    mocks.updateAlertRule.mockResolvedValue({});
    mocks.saveDowntimeWindow.mockResolvedValue({});
  });

  it("puts the worst level on the sensor, its group and the active-alarm list", async () => {
    renderWith(<SensorSettings />);
    const active = await screen.findByRole("region", { name: "Aktive Alarme" });
    expect(within(active).getByText("Speicher voll")).toBeInTheDocument();
    expect(within(active).getByText(/85.50/)).toBeInTheDocument();
    const table = screen.getByRole("region", { name: "Gruppen und Sensoren" });
    const row = within(table).getByText("storage.target.used_percent").closest("tr")!;
    expect(within(row).getByText("Warnung")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Alarmregeln für storage.target.used_percent" })).toHaveTextContent("1 Regel(n)");
    // The group header row shows the same worst level.
    const header = screen.getByText(/Kapazität/).closest("tr")!;
    expect(within(header).getByText("Warnung")).toBeInTheDocument();
  });

  it("expands a sensor to its rules and edits one with a live preview", async () => {
    mocks.previewAlertRule.mockResolvedValue({
      value: 34.5,
      level: "ok",
      reason: null,
      series: [{ labels: { target: "archive", service: "storage-service" }, value: 34.5 }],
    });
    renderWith(<SensorSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Alarmregeln für storage.target.used_percent" }));
    const rules = screen.getByLabelText("Regeln von storage.target.used_percent");
    expect(within(rules).getByText(/Warnung > 80 · Fehler > 95/)).toBeInTheDocument();
    fireEvent.click(within(rules).getByRole("button", { name: "Bearbeiten" }));

    const form = screen.getByRole("form", { name: "Alarmregel „Speicher voll“ bearbeiten" });
    fireEvent.click(within(form).getByRole("button", { name: "Aktuellen Wert prüfen" }));
    expect(await within(form).findByText(/Aktueller Wert: 34.50/)).toBeInTheDocument();
    // A label from the preview becomes the filter value.
    fireEvent.click(within(form).getByRole("button", { name: "target=archive" }));
    fireEvent.change(within(form).getByLabelText("Warnung über"), { target: { value: "70" } });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(mocks.updateAlertRule).toHaveBeenCalledWith(
        "tok",
        "r1",
        expect.objectContaining({ label_filter: { target: "archive" }, max_warning: 70, max_error: 95 })
      )
    );
  });

  it("refuses thresholds out of order before calling the server", async () => {
    renderWith(
      <AlertRuleForm token="tok" sensor="storage.target.used_percent" sensorActive={false} onDone={() => undefined} />
    );
    const form = screen.getByRole("form", { name: "Neue Alarmregel für storage.target.used_percent" });
    expect(within(form).getByText(/Sensor ist gerade aus/)).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("Warnung über"), { target: { value: "90" } });
    fireEvent.change(within(form).getByLabelText("Fehler über"), { target: { value: "80" } });
    fireEvent.submit(form);
    expect(await within(form).findByRole("alert")).toHaveTextContent("aufsteigend");
    expect(mocks.createAlertRule).not.toHaveBeenCalled();
  });

  it("is read-only without admin.monitoring", async () => {
    mocks.permissions = [];
    renderWith(<SensorSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Alarmregeln für storage.target.used_percent" }));
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Alarmregel hinzufügen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Wartungsfenster anlegen" })).not.toBeInTheDocument();
  });

  it("creates a weekly overnight window for selected rules", async () => {
    const onChange = vi.fn().mockResolvedValue(undefined);
    renderWith(<DowntimeWindows token="tok" windows={[]} rules={[rule()]} canManage onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Wartungsfenster anlegen" }));
    const form = screen.getByRole("form", { name: "Neues Wartungsfenster" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Nachtsicherung" } });
    fireEvent.click(within(form).getByLabelText("So"));
    fireEvent.change(within(form).getByLabelText("Täglich ab"), { target: { value: "22:00" } });
    fireEvent.change(within(form).getByLabelText("bis"), { target: { value: "02:00" } });
    fireEvent.click(within(form).getByLabelText("Alle Regeln"));
    fireEvent.click(within(form).getByLabelText("Speicher voll"));
    fireEvent.submit(form);

    await waitFor(() =>
      expect(mocks.saveDowntimeWindow).toHaveBeenCalledWith(
        "tok",
        {
          name: "Nachtsicherung",
          starts_at: null,
          ends_at: null,
          weekdays: [6],
          daily_start: "22:00",
          daily_end: "02:00",
          rule_ids: ["r1"],
          enabled: true,
        },
        undefined
      )
    );
    expect(onChange).toHaveBeenCalled();
  });

  it("lists windows with their schedule and state", () => {
    renderWith(
      <DowntimeWindows
        token="tok"
        windows={[
          {
            id: "w1",
            name: "Wartung",
            starts_at: null,
            ends_at: null,
            weekdays: [0, 2],
            daily_start: "06:00",
            daily_end: "07:00",
            rule_ids: null,
            enabled: true,
            active: true,
            updated_at: "",
            updated_by: null,
          },
        ]}
        rules={[]}
        canManage={false}
        onChange={async () => undefined}
      />
    );
    const item = screen.getByText("Wartung").closest("li")!;
    expect(within(item).getByText("aktiv")).toBeInTheDocument();
    expect(within(item).getByText("Mo, Mi · 06:00–07:00")).toBeInTheDocument();
    expect(within(item).getByText("Alle Regeln")).toBeInTheDocument();
  });

  it("helpers: worst level, thresholds, problems, window input", () => {
    expect(worstLevel([])).toBeNull();
    expect(worstLevel([rule({ level: "ok" }), rule({ level: "error" }), rule({ level: "warning" })])).toBe("error");
    expect(worstLevel([rule({ level: "error", enabled: false }), rule({ level: "ok" })])).toBe("ok");
    const t = (k: string) => ({ "alerts.levels.warning": "Warnung", "alerts.levels.error": "Fehler" })[k] ?? k;
    expect(thresholdsSummary(rule({ min_error: 1 }), t)).toBe("Fehler < 1 · Warnung > 80 · Fehler > 95");
    expect(ruleProblem({ ...rule(), max_warning: null, max_error: null })).toBe("alerts.problems.noThreshold");
    expect(ruleProblem({ ...rule(), warning_repeat: 0 })).toBe("alerts.problems.repeat");
    expect(ruleProblem(rule())).toBeNull();
    expect(
      toWindowInput({
        name: " x ",
        startsAt: "",
        endsAt: "",
        weekdays: [3, 1],
        dailyStart: "",
        dailyEnd: "",
        allRules: true,
        ruleIds: ["r1"],
        enabled: true,
      })
    ).toEqual({
      name: "x",
      starts_at: null,
      ends_at: null,
      weekdays: [1, 3],
      daily_start: null,
      daily_end: null,
      rule_ids: null,
      enabled: true,
    });
  });

  it("says when there are no rules yet", () => {
    renderWith(<ActiveAlerts rules={[]} />);
    expect(screen.getByText(/Noch keine Alarmregeln/)).toBeInTheDocument();
  });
});
