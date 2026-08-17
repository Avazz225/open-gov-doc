import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "@/app/login/page";
import { I18nProvider } from "@/i18n";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

const loginMock = vi.fn();
let mockUser: { username: string } | null = null;

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      login: loginMock,
      user: mockUser,
      isLoading: false,
    }),
  };
});

const getSsoConfigMock = vi.fn();
const oidcAuthorizeMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getSsoConfig: (...args: unknown[]) => getSsoConfigMock(...args),
    oidcAuthorize: (...args: unknown[]) => oidcAuthorizeMock(...args),
  };
});

function renderLoginPage() {
  return render(
    <I18nProvider>
      <LoginPage />
    </I18nProvider>
  );
}

// Direct links (post-roadmap feature, Phase 27, ADR 0106): the login flow
// must send the user back to the resource they were trying to reach,
// carried via a "returnTo" query param set by RequireAuth.tsx. See
// login-callback-page.test.tsx for the SSO round-trip variant (P27-S3).
describe("LoginPage returnTo (Phase 27)", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    loginMock.mockReset();
    getSsoConfigMock.mockReset();
    oidcAuthorizeMock.mockReset();
    getSsoConfigMock.mockResolvedValue({ enabled: false });
    mockUser = null;
    window.sessionStorage.clear();
  });

  it("redirects to the returnTo target after a successful manual login", async () => {
    window.history.pushState({}, "", "/login/?returnTo=%2Fworkspace%2Ffoo");
    loginMock.mockResolvedValue(undefined);

    renderLoginPage();
    await screen.findByLabelText("Benutzername");
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalledWith("alice", "secret"));
    expect(replaceMock).toHaveBeenCalledWith("/workspace/foo");
  });

  it("defaults to / when no returnTo is present", async () => {
    window.history.pushState({}, "", "/login/");
    loginMock.mockResolvedValue(undefined);

    renderLoginPage();
    await screen.findByLabelText("Benutzername");
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalled());
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("falls back to / for a protocol-relative returnTo (open-redirect protection)", async () => {
    window.history.pushState({}, "", "/login/?returnTo=%2F%2Fevil.example.com");
    loginMock.mockResolvedValue(undefined);

    renderLoginPage();
    await screen.findByLabelText("Benutzername");
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalled());
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("stashes the sanitized returnTo in sessionStorage before an SSO redirect (P27-S3)", async () => {
    window.history.pushState({}, "", "/login/?returnTo=%2Fworkspace%2Ffoo");
    getSsoConfigMock.mockResolvedValue({ enabled: true });
    oidcAuthorizeMock.mockResolvedValue("https://keycloak.example.com/authorize?...");

    renderLoginPage();

    await waitFor(() =>
      expect(window.sessionStorage.getItem("dms.sso.returnTo")).toBe("/workspace/foo")
    );
    expect(window.sessionStorage.getItem("dms.sso.state")).not.toBeNull();
  });
});
