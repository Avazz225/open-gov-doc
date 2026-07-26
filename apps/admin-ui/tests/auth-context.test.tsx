import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/lib/auth-context";

const loginMock = vi.fn();
const getCurrentUserMock = vi.fn();

vi.mock("@/lib/api", () => ({
  login: (...args: unknown[]) => loginMock(...args),
  getCurrentUser: (...args: unknown[]) => getCurrentUserMock(...args),
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
  const { user, isLoading, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(isLoading)}</span>
      <span data-testid="user">{user?.username ?? "none"}</span>
      <button onClick={() => login("admin", "secret")}>login</button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

describe("AuthProvider (admin-ui)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    loginMock.mockReset();
    getCurrentUserMock.mockReset();
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

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));

    await act(async () => {
      screen.getByText("login").click();
    });

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("admin"));
    expect(window.localStorage.getItem("dms.tokens")).not.toBeNull();
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

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    await act(async () => {
      screen.getByText("login").click();
    });
    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("admin"));

    await act(async () => {
      screen.getByText("logout").click();
    });

    expect(screen.getByTestId("user").textContent).toBe("none");
    expect(window.localStorage.getItem("dms.tokens")).toBeNull();
  });

  it("restores a still-valid session from localStorage on mount", async () => {
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
      username: "restored-admin",
      email: null,
      realm_roles: [],
    });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("user").textContent).toBe("restored-admin"));
  });
});
