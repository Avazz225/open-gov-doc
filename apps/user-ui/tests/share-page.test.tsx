import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SharePage from "@/app/share/page";
import { I18nProvider } from "@/i18n";
import { ApiError } from "@/lib/api";

const getPublicShareLinkMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getPublicShareLink: (...args: unknown[]) => getPublicShareLinkMock(...args),
  };
});

function renderSharePage() {
  return render(
    <I18nProvider>
      <SharePage />
    </I18nProvider>
  );
}

// Öffentliche Seite (4.2a, P14-S10) - bewusst OHNE RequireAuth/useAuth-Mock,
// da genau das der Punkt dieser Seite ist (kein eingeloggter Nutzer nötig).
describe("SharePage", () => {
  beforeEach(() => {
    getPublicShareLinkMock.mockReset();
  });

  it("shows a missing-token message when opened without a query string", async () => {
    window.history.pushState({}, "", "/share");

    renderSharePage();

    expect(await screen.findByText("Kein Freigabelink-Token angegeben.")).toBeInTheDocument();
  });

  it("loads and shows the document title, expiry and a download link", async () => {
    window.history.pushState({}, "", "/share?token=tok-abc");
    getPublicShareLinkMock.mockResolvedValue({
      title: "Vertrag.pdf",
      content_type: "application/pdf",
      size_bytes: 1234,
      expires_at: "2026-12-31T00:00:00Z",
    });

    renderSharePage();

    expect(await screen.findByText("Vertrag.pdf")).toBeInTheDocument();
    expect(getPublicShareLinkMock).toHaveBeenCalledWith("tok-abc");
    const downloadLink = screen.getByText("Herunterladen") as HTMLAnchorElement;
    expect(downloadLink.href).toContain("token=tok-abc");
  });

  it("shows the server error message for an expired or revoked link", async () => {
    window.history.pushState({}, "", "/share?token=tok-expired");
    getPublicShareLinkMock.mockRejectedValue(new ApiError(410, "Freigabelink abgelaufen"));

    renderSharePage();

    expect(await screen.findByText("Freigabelink abgelaufen")).toBeInTheDocument();
  });
});
