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

// Direct links (post-roadmap feature, Phase 27, ADR 0106).
describe("LoginPage returnTo (Phase 27)", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    loginMock.mockReset();
    mockUser = null;
  });

  it("redirects to the returnTo target after a successful manual login", async () => {
    window.history.pushState({}, "", "/login/?returnTo=%2F%3Finstance%3Dabc-123");
    loginMock.mockResolvedValue(undefined);

    renderLoginPage();
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalledWith("alice", "secret"));
    expect(replaceMock).toHaveBeenCalledWith("/?instance=abc-123");
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
});
