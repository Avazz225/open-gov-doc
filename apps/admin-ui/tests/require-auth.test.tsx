import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RequireAuth } from "@/components/RequireAuth";
import { I18nProvider } from "@/i18n";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
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
      <RequireAuth>
        <p>secret content</p>
      </RequireAuth>
    </I18nProvider>
  );
}

// Direct links (post-roadmap feature, Phase 27, ADR 0106): counterpart to
// login-page.test.tsx's returnTo consumption.
describe("RequireAuth returnTo (Phase 27)", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    mockUser = null;
  });

  it("redirects to login with the current path+query as returnTo when logged out", () => {
    window.history.pushState({}, "", "/users/?tab=roles");

    renderProtected();

    expect(replaceMock).toHaveBeenCalledWith("/login/?returnTo=%2Fusers%2F%3Ftab%3Droles");
  });

  it("does not redirect and renders children when logged in", () => {
    window.history.pushState({}, "", "/users/");
    mockUser = { username: "alice" };

    const { getByText } = renderProtected();

    expect(replaceMock).not.toHaveBeenCalled();
    expect(getByText("secret content")).toBeInTheDocument();
  });
});
