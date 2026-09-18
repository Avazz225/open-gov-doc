import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth-context";
import { LocaleProvider, useLocale } from "@/lib/locale-context";

const getLocalePreferenceMock = vi.fn();
const updateLocalePreferenceMock = vi.fn();

vi.mock("@/lib/api", () => ({
  login: vi.fn(),
  refreshToken: vi.fn(),
  getCurrentUser: vi.fn().mockResolvedValue({
    sub: "u1",
    username: "alice",
    email: "alice@example.com",
    realm_roles: [],
  }),
  getLocalePreference: (...args: unknown[]) => getLocalePreferenceMock(...args),
  updateLocalePreference: (...args: unknown[]) => updateLocalePreferenceMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

function Probe() {
  const { locale, setLocale } = useLocale();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <button onClick={() => setLocale("en")}>set-en</button>
    </div>
  );
}

function renderWithProviders() {
  return render(
    <AuthProvider>
      <LocaleProvider>
        <Probe />
      </LocaleProvider>
    </AuthProvider>
  );
}

describe("LocaleProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "de";
    getLocalePreferenceMock.mockReset();
    updateLocalePreferenceMock.mockReset().mockResolvedValue(undefined);
  });

  it("defaults to de and applies it as the document lang attribute", () => {
    renderWithProviders();

    expect(screen.getByTestId("locale").textContent).toBe("de");
    expect(document.documentElement.lang).toBe("de");
  });

  it("restores a cached locale from localStorage after mount, not as the initial render", () => {
    // Regression test for the real hydration bug found live during
    // process-designer's P47-S1 (React error #418): unlike `ThemeProvider`,
    // which can safely read `localStorage` as the initial `useState` value
    // (theme only drives an imperative attribute), the static export always
    // server-renders `defaultLocale`'s strings - seeding the initial state
    // from a cached non-default locale would mismatch that markup on
    // hydration. The cached value is applied in a `useEffect` instead,
    // always one tick after the first render.
    window.localStorage.setItem("dms.locale", "en");

    renderWithProviders();

    expect(screen.getByTestId("locale").textContent).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("setLocale updates the document attribute and persists to localStorage", async () => {
    renderWithProviders();
    expect(screen.getByTestId("locale").textContent).toBe("de");

    await act(async () => {
      screen.getByText("set-en").click();
    });

    expect(screen.getByTestId("locale").textContent).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    expect(window.localStorage.getItem("dms.locale")).toBe("en");
  });

  afterEach(() => {
    document.documentElement.lang = "de";
  });
});
