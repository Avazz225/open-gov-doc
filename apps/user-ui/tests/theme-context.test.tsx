import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider, useTheme } from "@/lib/theme-context";

const getThemePreferenceMock = vi.fn();
const updateThemePreferenceMock = vi.fn();

vi.mock("@/lib/api", () => ({
  login: vi.fn(),
  refreshToken: vi.fn(),
  getCurrentUser: vi.fn().mockResolvedValue({
    sub: "u1",
    username: "alice",
    email: "alice@example.com",
    realm_roles: [],
  }),
  getThemePreference: (...args: unknown[]) => getThemePreferenceMock(...args),
  updateThemePreference: (...args: unknown[]) => updateThemePreferenceMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

function Probe() {
  const { theme, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <button onClick={() => setTheme("dark")}>set-dark</button>
    </div>
  );
}

function renderWithProviders() {
  return render(
    <AuthProvider>
      <ThemeProvider>
        <Probe />
      </ThemeProvider>
    </AuthProvider>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    getThemePreferenceMock.mockReset();
    updateThemePreferenceMock.mockReset().mockResolvedValue(undefined);
  });

  it("defaults to auto and applies it as the document theme attribute", async () => {
    renderWithProviders();

    await waitFor(() => expect(screen.getByTestId("theme").textContent).toBe("auto"));
    expect(["light", "dark"]).toContain(document.documentElement.dataset.theme);
  });

  it("restores a cached theme from localStorage before any login", () => {
    window.localStorage.setItem("dms.theme", "high-contrast");

    renderWithProviders();

    expect(screen.getByTestId("theme").textContent).toBe("high-contrast");
    expect(document.documentElement.dataset.theme).toBe("high-contrast");
  });

  it("setTheme updates the document attribute and persists to localStorage", async () => {
    renderWithProviders();
    await waitFor(() => expect(screen.getByTestId("theme").textContent).toBe("auto"));

    await act(async () => {
      screen.getByText("set-dark").click();
    });

    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("dms.theme")).toBe("dark");
  });

  afterEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });
});
