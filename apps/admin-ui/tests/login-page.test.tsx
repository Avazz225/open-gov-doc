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

function renderLoginPage() {
  return render(
    <I18nProvider>
      <LoginPage />
    </I18nProvider>
  );
}

// Direct links (post-roadmap feature, Phase 27, ADR 0106): the login flow
// must send the user back to the resource they were trying to reach,
// carried via a "returnTo" query param set by RequireAuth.tsx - see
// require-auth.test.tsx for the redirect-to-login side of this roundtrip.
describe("LoginPage returnTo (Phase 27)", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    loginMock.mockReset();
    mockUser = null;
  });

  it("redirects to the returnTo target after a successful manual login", async () => {
    window.history.pushState({}, "", "/login/?returnTo=%2Fusers%2F");
    loginMock.mockResolvedValue(undefined);

    renderLoginPage();
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalledWith("alice", "secret"));
    expect(replaceMock).toHaveBeenCalledWith("/users/");
  });

  it("defaults to / when no returnTo is present", async () => {
    window.history.pushState({}, "", "/login/");
    loginMock.mockResolvedValue(undefined);

    renderLoginPage();
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
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalled());
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("falls back to / for an absolute-URL returnTo", async () => {
    window.history.pushState(
      {},
      "",
      "/login/?returnTo=" + encodeURIComponent("https://evil.example.com")
    );
    loginMock.mockResolvedValue(undefined);

    renderLoginPage();
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalled());
    expect(replaceMock).toHaveBeenCalledWith("/");
  });

  it("redirects an already-logged-in visitor straight to returnTo", () => {
    window.history.pushState({}, "", "/login/?returnTo=%2Fusers%2F");
    mockUser = { username: "alice" };

    renderLoginPage();

    expect(replaceMock).toHaveBeenCalledWith("/users/");
  });
});
