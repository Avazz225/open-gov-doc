import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RequireAuth } from "@/components/RequireAuth";
import { I18nProvider } from "@/i18n";
import { ThemeProvider } from "@/lib/theme-context";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
  usePathname: () => "/",
}));

let mockUser: { username: string } | null = null;

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: mockUser,
      isLoading: false,
    }),
  };
});

function renderProtected() {
  return render(
    <I18nProvider>
      <ThemeProvider>
        <RequireAuth>
          <p>secret content</p>
        </RequireAuth>
      </ThemeProvider>
    </I18nProvider>
  );
}

// Direct links (post-roadmap feature, Phase 27, ADR 0106): counterpart to
// user-ui/admin-ui's login-page.test.tsx returnTo consumption.
describe("RequireAuth returnTo (Phase 27)", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    mockUser = null;
  });

  it("redirects to login with the current path+query as returnTo when logged out", () => {
    window.history.pushState({}, "", "/?instance=abc-123");

    renderProtected();

    expect(replaceMock).toHaveBeenCalledWith("/login/?returnTo=%2F%3Finstance%3Dabc-123");
  });

  it("does not redirect and renders children when logged in", () => {
    window.history.pushState({}, "", "/");
    mockUser = { username: "alice" };

    const { getByText } = renderProtected();

    expect(replaceMock).not.toHaveBeenCalled();
    expect(getByText("secret content")).toBeInTheDocument();
  });
});
