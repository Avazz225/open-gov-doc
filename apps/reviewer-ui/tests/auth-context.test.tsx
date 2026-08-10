import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/lib/auth-context";

const loginMock = vi.fn();
const getCurrentUserMock = vi.fn();
const getEffectivePermissionsMock = vi.fn();

vi.mock("@/lib/api", () => ({
  login: (...args: unknown[]) => loginMock(...args),
  getCurrentUser: (...args: unknown[]) => getCurrentUserMock(...args),
  getEffectivePermissions: (...args: unknown[]) => getEffectivePermissionsMock(...args),
  refreshToken: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

function Probe() {
  const { user, permissions, accessToken, isLoading, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(isLoading)}</span>
      <span data-testid="user">{user?.username ?? "none"}</span>
      <span data-testid="token">{accessToken ?? "none"}</span>
      <span data-testid="permissions">{permissions.join(",")}</span>
      <button onClick={() => login("alice", "secret")}>login</button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

describe("AuthProvider (reviewer-ui)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    loginMock.mockReset();
    getCurrentUserMock.mockReset();
    getEffectivePermissionsMock.mockReset();
    getEffectivePermissionsMock.mockResolvedValue([]);
  });

  it("starts logged out with isLoading resolved to false when no stored session exists", async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    expect(screen.getByTestId("user").textContent).toBe("none");
  });

  it("login stores the session and exposes the effective capabilities (4.6)", async () => {
    loginMock.mockResolvedValue({
      access_token: "access-1",
      refresh_token: "refresh-1",
      expires_in: 300,
      token_type: "Bearer",
    });
    getCurrentUserMock.mockResolvedValue({
      sub: "u1",
      username: "alice",
      email: "alice@example.com",
      realm_roles: [],
    });
    getEffectivePermissionsMock.mockResolvedValue(["admin.object_config"]);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));

    await act(async () => {
      screen.getByText("login").click();
    });

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("alice"));
    expect(screen.getByTestId("token").textContent).toBe("access-1");
    expect(screen.getByTestId("permissions").textContent).toBe("admin.object_config");
    expect(window.localStorage.getItem("dms.tokens")).not.toBeNull();
  });

  it("logout clears the stored session and permissions", async () => {
    loginMock.mockResolvedValue({
      access_token: "access-1",
      refresh_token: "refresh-1",
      expires_in: 300,
      token_type: "Bearer",
    });
    getCurrentUserMock.mockResolvedValue({
      sub: "u1",
      username: "alice",
      email: null,
      realm_roles: [],
    });
    getEffectivePermissionsMock.mockResolvedValue(["admin.object_config"]);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => {
      screen.getByText("login").click();
    });
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("alice"));

    await act(async () => {
      screen.getByText("logout").click();
    });

    expect(screen.getByTestId("user").textContent).toBe("none");
    expect(screen.getByTestId("permissions").textContent).toBe("");
    expect(window.localStorage.getItem("dms.tokens")).toBeNull();
  });

  it("restores a still-valid session and its permissions from localStorage on mount", async () => {
    window.localStorage.setItem(
      "dms.tokens",
      JSON.stringify({
        accessToken: "stored-access",
        refreshToken: "stored-refresh",
        expiresAt: Date.now() + 60_000,
      })
    );
    getCurrentUserMock.mockResolvedValue({
      sub: "u1",
      username: "bob",
      email: null,
      realm_roles: [],
    });
    getEffectivePermissionsMock.mockResolvedValue(["admin.object_config"]);

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("bob"));
    expect(screen.getByTestId("permissions").textContent).toBe("admin.object_config");
  });
});
