import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth-context";
import { BrandingProvider, useBranding } from "@/lib/branding-context";
import { InstallationProvider } from "@/lib/installation-context";
import { ThemeProvider } from "@/lib/theme-context";

const getBrandingConfigMock = vi.fn();
const getThemePreferenceMock = vi.fn();

vi.mock("@/lib/api", () => ({
  login: vi.fn(),
  refreshToken: vi.fn(),
  setGatewayBaseUrl: vi.fn(),
  getCurrentUser: vi.fn().mockResolvedValue({
    sub: "u1",
    username: "admin",
    email: "admin@example.com",
    realm_roles: [],
  }),
  getThemePreference: (...args: unknown[]) => getThemePreferenceMock(...args),
  updateThemePreference: vi.fn(),
  getBrandingConfig: (...args: unknown[]) => getBrandingConfigMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

function Probe() {
  const { productName, accentColor } = useBranding();
  return (
    <div>
      <span data-testid="product-name">{productName ?? "null"}</span>
      <span data-testid="accent-color">{accentColor ?? "null"}</span>
    </div>
  );
}

function renderWithProviders() {
  return render(
    <InstallationProvider>
      <AuthProvider>
        <ThemeProvider>
          <BrandingProvider>
            <Probe />
          </BrandingProvider>
        </ThemeProvider>
      </AuthProvider>
    </InstallationProvider>
  );
}

// Installation-level branding (7.3/8, P69-S2, ADR 0201).
describe("BrandingProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.removeProperty("--dms-accent");
    document.documentElement.style.removeProperty("--dms-accent-bg");
    document.documentElement.style.removeProperty("--dms-accent-bg-strong");
    getThemePreferenceMock.mockReset();
    getBrandingConfigMock.mockReset();
  });

  it("applies the fetched product_name to document.title", async () => {
    getBrandingConfigMock.mockResolvedValue({
      product_name: "Custom Product",
      accent_color: null,
      logo_url: null,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderWithProviders();

    await waitFor(() => expect(screen.getByTestId("product-name").textContent).toBe(
      "Custom Product"
    ));
    expect(document.title).toBe("Custom Product");
  });

  it("applies the fetched accent_color as a CSS custom property override", async () => {
    getBrandingConfigMock.mockResolvedValue({
      product_name: null,
      accent_color: "#ff0000",
      logo_url: null,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderWithProviders();

    await waitFor(() =>
      expect(document.documentElement.style.getPropertyValue("--dms-accent")).toBe("#ff0000")
    );
    expect(document.documentElement.style.getPropertyValue("--dms-accent-bg")).toBe(
      "rgba(255, 0, 0, 0.15)"
    );
  });

  it("does not override the accent tokens in high-contrast mode", async () => {
    window.localStorage.setItem("dms.theme", "high-contrast");
    getBrandingConfigMock.mockResolvedValue({
      product_name: null,
      accent_color: "#ff0000",
      logo_url: null,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderWithProviders();

    await waitFor(() => expect(screen.getByTestId("accent-color").textContent).toBe("#ff0000"));
    expect(document.documentElement.style.getPropertyValue("--dms-accent")).toBe("");
  });

  it("leaves document.title untouched when product_name is null", async () => {
    getBrandingConfigMock.mockResolvedValue({
      product_name: null,
      accent_color: null,
      logo_url: null,
      updated_at: "2026-01-01T00:00:00Z",
    });
    document.title = "Original Title";

    renderWithProviders();

    await waitFor(() => expect(getBrandingConfigMock).toHaveBeenCalled());
    expect(document.title).toBe("Original Title");
  });
});
