import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserTracking } from "@/components/UserTracking";
import { I18nProvider } from "@/i18n";

function renderUserTracking() {
  return render(
    <I18nProvider>
      <UserTracking />
    </I18nProvider>
  );
}

const getUserTrackingConfigMock = vi.fn();
const setUserTrackingConfigMock = vi.fn();
const listUserTrackingSessionsMock = vi.fn();
const getUserTrackingRetentionConfigMock = vi.fn();
const setUserTrackingRetentionConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getUserTrackingConfig: (...args: unknown[]) => getUserTrackingConfigMock(...args),
  setUserTrackingConfig: (...args: unknown[]) => setUserTrackingConfigMock(...args),
  listUserTrackingSessions: (...args: unknown[]) => listUserTrackingSessionsMock(...args),
  getUserTrackingRetentionConfig: (...args: unknown[]) => getUserTrackingRetentionConfigMock(...args),
  setUserTrackingRetentionConfig: (...args: unknown[]) => setUserTrackingRetentionConfigMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

let mockPermissions: string[] = ["admin.user_tracking", "admin.user_tracking_view"];

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "admin-1", username: "admin", email: null, realm_roles: [] },
      permissions: mockPermissions,
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const SESSION_A = {
  id: "s1",
  principal_id: "alice",
  username: "alice.doe",
  event_type: "login",
  auth_method: "keycloak",
  client_ip: "10.0.0.1",
  user_agent: "Mozilla/5.0",
  occurred_at: "2026-01-01T00:00:00Z",
};

const RETENTION_CONFIG = { retention_days: 7, updated_at: "2026-01-01T00:00:00Z" };

describe("UserTracking", () => {
  beforeEach(() => {
    getUserTrackingConfigMock.mockReset();
    setUserTrackingConfigMock.mockReset();
    listUserTrackingSessionsMock.mockReset();
    getUserTrackingRetentionConfigMock.mockReset();
    setUserTrackingRetentionConfigMock.mockReset();
    mockPermissions = ["admin.user_tracking", "admin.user_tracking_view"];

    listUserTrackingSessionsMock.mockResolvedValue([]);
    getUserTrackingRetentionConfigMock.mockResolvedValue(RETENTION_CONFIG);
  });

  it("shows only the config/retention sections without the view capability", async () => {
    mockPermissions = ["admin.user_tracking"];

    renderUserTracking();

    expect(await screen.findByText("Tracking-Einstellung je Principal")).toBeInTheDocument();
    expect(screen.getByText("Aufbewahrungsfrist")).toBeInTheDocument();
    expect(screen.queryByText("Erfasste Sitzungen")).not.toBeInTheDocument();
    expect(listUserTrackingSessionsMock).not.toHaveBeenCalled();
  });

  it("shows only the sessions section without the manage capability", async () => {
    mockPermissions = ["admin.user_tracking_view"];

    renderUserTracking();

    expect(await screen.findByText("Erfasste Sitzungen")).toBeInTheDocument();
    expect(screen.queryByText("Tracking-Einstellung je Principal")).not.toBeInTheDocument();
    expect(screen.queryByText("Aufbewahrungsfrist")).not.toBeInTheDocument();
    expect(getUserTrackingRetentionConfigMock).not.toHaveBeenCalled();
  });

  it("loads a principal's config on lookup and toggles it", async () => {
    getUserTrackingConfigMock.mockResolvedValue({
      principal_id: "alice",
      enabled: false,
      updated_by: "",
      updated_at: "2026-01-01T00:00:00Z",
    });
    setUserTrackingConfigMock.mockResolvedValue({
      status: "applied",
      config: {
        principal_id: "alice",
        enabled: true,
        updated_by: "admin-1",
        updated_at: "2026-01-02T00:00:00Z",
      },
      approval_request_id: null,
    });

    renderUserTracking();
    await waitFor(() => expect(getUserTrackingRetentionConfigMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Tracking-Einstellung laden" });
    fireEvent.change(within(form).getByLabelText("Principal-ID"), { target: { value: "alice" } });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(getUserTrackingConfigMock).toHaveBeenCalledWith("token-123", "alice")
    );
    const checkbox = await screen.findByLabelText("Tracking aktiv");
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);

    await waitFor(() =>
      expect(setUserTrackingConfigMock).toHaveBeenCalledWith("token-123", "alice", {
        enabled: true,
        updatedBy: "admin-1",
      })
    );
    await waitFor(() => expect(checkbox).toBeChecked());
  });

  it("shows a pending-approval hint when the four-eyes gate is active, without applying the toggle", async () => {
    getUserTrackingConfigMock.mockResolvedValue({
      principal_id: "alice",
      enabled: false,
      updated_by: "",
      updated_at: "2026-01-01T00:00:00Z",
    });
    setUserTrackingConfigMock.mockResolvedValue({
      status: "pending_approval",
      config: null,
      approval_request_id: "req-1",
    });

    renderUserTracking();
    await waitFor(() => expect(getUserTrackingRetentionConfigMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Tracking-Einstellung laden" });
    fireEvent.change(within(form).getByLabelText("Principal-ID"), { target: { value: "alice" } });
    fireEvent.submit(form);

    const checkbox = await screen.findByLabelText("Tracking aktiv");
    fireEvent.click(checkbox);

    await screen.findByText(
      "Vier-Augen-Prinzip aktiv - die Änderung wartet auf Genehmigung durch eine zweite Person, bevor sie wirksam wird."
    );
    // Not yet applied - the checkbox still reflects the pre-toggle value.
    expect(checkbox).not.toBeChecked();
  });

  it("lists sessions on mount and re-filters by principal", async () => {
    listUserTrackingSessionsMock.mockResolvedValueOnce([SESSION_A]);

    renderUserTracking();

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(listUserTrackingSessionsMock).toHaveBeenCalledWith("token-123", undefined);

    listUserTrackingSessionsMock.mockResolvedValueOnce([]);
    const form = screen.getByRole("form", { name: "Sitzungen filtern" });
    fireEvent.change(within(form).getByLabelText("Principal-ID"), { target: { value: "bob" } });
    fireEvent.submit(form);

    await waitFor(() => expect(listUserTrackingSessionsMock).toHaveBeenCalledWith("token-123", "bob"));
    expect(await screen.findByText("Keine erfassten Sitzungen.")).toBeInTheDocument();
  });

  it("loads and saves the retention period", async () => {
    setUserTrackingRetentionConfigMock.mockResolvedValue({
      retention_days: 14,
      updated_at: "2026-01-03T00:00:00Z",
    });

    renderUserTracking();
    const input = await screen.findByLabelText("Aufbewahrungsfrist (Tage)");
    expect(input).toHaveValue(7);

    fireEvent.change(input, { target: { value: "14" } });
    fireEvent.submit(screen.getByRole("form", { name: "Aufbewahrungsfrist speichern" }));

    await waitFor(() =>
      expect(setUserTrackingRetentionConfigMock).toHaveBeenCalledWith("token-123", 14)
    );
    expect(await screen.findByText(/Zuletzt geändert am/)).toBeInTheDocument();
  });

  it("rejects a retention period below 1 day client-side", async () => {
    renderUserTracking();
    const input = await screen.findByLabelText("Aufbewahrungsfrist (Tage)");

    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.submit(screen.getByRole("form", { name: "Aufbewahrungsfrist speichern" }));

    expect(
      await screen.findByText("Aufbewahrungsfrist muss mindestens 1 Tag betragen.")
    ).toBeInTheDocument();
    expect(setUserTrackingRetentionConfigMock).not.toHaveBeenCalled();
  });

  it("shows an error when the config lookup fails", async () => {
    getUserTrackingConfigMock.mockRejectedValue(new Error("boom"));

    renderUserTracking();
    await waitFor(() => expect(getUserTrackingRetentionConfigMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Tracking-Einstellung laden" });
    fireEvent.change(within(form).getByLabelText("Principal-ID"), { target: { value: "alice" } });
    fireEvent.submit(form);

    expect(await screen.findByText("Laden fehlgeschlagen")).toBeInTheDocument();
  });
});
