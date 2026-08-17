import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginCallbackPage from "@/app/login/callback/page";
import { I18nProvider } from "@/i18n";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

const applySessionMock = vi.fn();
vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      applySession: applySessionMock,
    }),
  };
});

const oidcCallbackMock = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    oidcCallback: (...args: unknown[]) => oidcCallbackMock(...args),
  };
});

function renderCallbackPage() {
  return render(
    <I18nProvider>
      <LoginCallbackPage />
    </I18nProvider>
  );
}

// Direct links (post-roadmap feature, Phase 27, ADR 0106, P27-S3): the SSO
// round trip stashes returnTo in sessionStorage before redirecting to
// Keycloak (login-page.test.tsx) since the redirect_uri itself can't carry
// it (ADR 0062's fixed origin allow-list) - this is the read-back half.
describe("LoginCallbackPage returnTo (Phase 27 / P27-S3)", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    applySessionMock.mockReset();
    oidcCallbackMock.mockReset();
    window.sessionStorage.clear();
  });

  it("redirects to the stashed returnTo target after a successful SSO round trip", async () => {
    window.sessionStorage.setItem("dms.sso.state", "state-abc");
    window.sessionStorage.setItem("dms.sso.returnTo", "/workspace/foo");
    window.history.pushState({}, "", "/login/callback/?code=auth-code&state=state-abc");
    oidcCallbackMock.mockResolvedValue({ access_token: "tok" });
    applySessionMock.mockResolvedValue(undefined);

    renderCallbackPage();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/workspace/foo"));
    // Stashed values are consumed exactly once.
    expect(window.sessionStorage.getItem("dms.sso.returnTo")).toBeNull();
    expect(window.sessionStorage.getItem("dms.sso.state")).toBeNull();
  });

  it("defaults to / when no returnTo was stashed", async () => {
    window.sessionStorage.setItem("dms.sso.state", "state-abc");
    window.history.pushState({}, "", "/login/callback/?code=auth-code&state=state-abc");
    oidcCallbackMock.mockResolvedValue({ access_token: "tok" });
    applySessionMock.mockResolvedValue(undefined);

    renderCallbackPage();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
  });

  it("falls back to / for a tampered protocol-relative stashed returnTo", async () => {
    window.sessionStorage.setItem("dms.sso.state", "state-abc");
    window.sessionStorage.setItem("dms.sso.returnTo", "//evil.example.com");
    window.history.pushState({}, "", "/login/callback/?code=auth-code&state=state-abc");
    oidcCallbackMock.mockResolvedValue({ access_token: "tok" });
    applySessionMock.mockResolvedValue(undefined);

    renderCallbackPage();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
  });
});
