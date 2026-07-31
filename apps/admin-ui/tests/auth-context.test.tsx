import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { InstallationProvider, useInstallation } from "@/lib/installation-context";

const loginMock = vi.fn();
const getCurrentUserMock = vi.fn();
const getEffectivePermissionsMock = vi.fn();

vi.mock("@/lib/api", () => ({
  login: (...args: unknown[]) => loginMock(...args),
  getCurrentUser: (...args: unknown[]) => getCurrentUserMock(...args),
  getEffectivePermissions: (...args: unknown[]) => getEffectivePermissionsMock(...args),
  refreshToken: vi.fn(),
  setGatewayBaseUrl: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

function Probe() {
  const { user, permissions, isLoading, login, logout } = useAuth();
  const { installations, activeInstallation, switchInstallation, addInstallation } = useInstallation();
  return (
    <div>
      <span data-testid="loading">{String(isLoading)}</span>
      <span data-testid="user">{user?.username ?? "none"}</span>
      <span data-testid="permissions">{permissions.join(",")}</span>
      <span data-testid="active-installation">{activeInstallation.name}</span>
      <button onClick={() => login("admin", "secret")}>login</button>
      <button onClick={logout}>logout</button>
      <button
        onClick={() => addInstallation({ name: "Zweitinstallation", gatewayBaseUrl: "http://second:8009" })}
      >
        add-second
      </button>
      <button
        onClick={() => {
          const second = installations.find((i) => i.name === "Zweitinstallation");
          if (second) switchInstallation(second.id);
        }}
      >
        switch-to-second
      </button>
      <button onClick={() => switchInstallation("default")}>switch-to-default</button>
    </div>
  );
}

function renderProbe() {
  return render(
    <InstallationProvider>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </InstallationProvider>
  );
}

describe("AuthProvider (admin-ui)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    loginMock.mockReset();
    getCurrentUserMock.mockReset();
    getEffectivePermissionsMock.mockReset();
    getEffectivePermissionsMock.mockResolvedValue([]);
  });

  it("login stores the session and exposes the current user", async () => {
    loginMock.mockResolvedValue({
      access_token: "access-1",
      refresh_token: "refresh-1",
      expires_in: 300,
      token_type: "Bearer",
    });
    getCurrentUserMock.mockResolvedValue({
      sub: "u1",
      username: "admin",
      email: null,
      realm_roles: [],
    });

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));

    await act(async () => {
      screen.getByText("login").click();
    });

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("admin"));
    expect(window.localStorage.getItem("dms.tokens.default")).not.toBeNull();
  });

  it("exposes the domain-admin capabilities loaded after login (4.6)", async () => {
    loginMock.mockResolvedValue({
      access_token: "access-1",
      refresh_token: "refresh-1",
      expires_in: 300,
      token_type: "Bearer",
    });
    getCurrentUserMock.mockResolvedValue({
      sub: "u1",
      username: "admin",
      email: null,
      realm_roles: [],
    });
    getEffectivePermissionsMock.mockResolvedValue(["admin.user_management"]);

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => {
      screen.getByText("login").click();
    });

    await waitFor(() =>
      expect(screen.getByTestId("permissions").textContent).toBe("admin.user_management")
    );
  });

  it("logout clears the stored session", async () => {
    loginMock.mockResolvedValue({
      access_token: "access-1",
      refresh_token: "refresh-1",
      expires_in: 300,
      token_type: "Bearer",
    });
    getCurrentUserMock.mockResolvedValue({
      sub: "u1",
      username: "admin",
      email: null,
      realm_roles: [],
    });

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => {
      screen.getByText("login").click();
    });
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("admin"));

    await act(async () => {
      screen.getByText("logout").click();
    });

    expect(screen.getByTestId("user").textContent).toBe("none");
    expect(window.localStorage.getItem("dms.tokens.default")).toBeNull();
  });

  it("restores a still-valid session from localStorage on mount", async () => {
    window.localStorage.setItem(
      "dms.tokens.default",
      JSON.stringify({
        accessToken: "stored-access",
        refreshToken: "stored-refresh",
        expiresAt: Date.now() + 60_000,
      })
    );
    getCurrentUserMock.mockResolvedValue({
      sub: "u1",
      username: "restored-admin",
      email: null,
      realm_roles: [],
    });

    renderProbe();

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("restored-admin"));
  });

  it("keeps sessions isolated per installation and requires no re-login when switching back", async () => {
    // Vorbereitung: Installation "default" hat bereits eine gültige Sitzung.
    window.localStorage.setItem(
      "dms.tokens.default",
      JSON.stringify({
        accessToken: "default-access",
        refreshToken: "default-refresh",
        expiresAt: Date.now() + 60_000,
      })
    );
    getCurrentUserMock.mockImplementation(async (token: string) => {
      if (token === "default-access") {
        return { sub: "u1", username: "default-admin", email: null, realm_roles: [] };
      }
      if (token === "second-access") {
        return { sub: "u2", username: "second-admin", email: null, realm_roles: [] };
      }
      throw new Error("unexpected token");
    });
    loginMock.mockResolvedValue({
      access_token: "second-access",
      refresh_token: "second-refresh",
      expires_in: 300,
      token_type: "Bearer",
    });

    renderProbe();
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("default-admin"));

    await act(async () => {
      screen.getByText("add-second").click();
    });
    await act(async () => {
      screen.getByText("switch-to-second").click();
    });

    // Neue Installation hat noch keine Sitzung - Login nötig.
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("none"));
    await act(async () => {
      screen.getByText("login").click();
    });
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("second-admin"));

    // Zurück zur ersten Installation: keine erneute Anmeldung nötig, die
    // ursprüngliche Sitzung ist weiterhin gültig und wurde nicht angetastet.
    await act(async () => {
      screen.getByText("switch-to-default").click();
    });
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("default-admin"));
  });
});
