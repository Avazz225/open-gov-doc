import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ComponentInventory } from "@/components/ComponentInventory";
import { I18nProvider } from "@/i18n";

// P100-S1 (ADR 0271): software components per service.

const mocks = vi.hoisted(() => ({
  listComponentSummaries: vi.fn(),
  getServiceComponents: vi.fn(),
  searchComponents: vi.fn(),
  exportComponents: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  listComponentSummaries: (...a: unknown[]) => mocks.listComponentSummaries(...a),
  getServiceComponents: (...a: unknown[]) => mocks.getServiceComponents(...a),
  searchComponents: (...a: unknown[]) => mocks.searchComponents(...a),
  exportComponents: (...a: unknown[]) => mocks.exportComponents(...a),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
      permissions: ["admin.monitoring"],
      accessToken: "tok",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const SUMMARY = {
  service: "document-service",
  kind: "service",
  instances: 2,
  component_count: 2,
  ecosystems: { Debian: 1, PyPI: 1 },
  replicas_consistent: false,
  matches_image: true,
  image_created: "2026-11-27T10:00:00+00:00",
  error: null,
};

function renderPage() {
  return render(
    <I18nProvider>
      <ComponentInventory />
    </I18nProvider>
  );
}

describe("ComponentInventory", () => {
  beforeEach(() => {
    mocks.listComponentSummaries
      .mockReset()
      .mockResolvedValue([
        SUMMARY,
        {
          ...SUMMARY,
          service: "admin-ui",
          kind: "frontend",
          instances: 1,
          ecosystems: { npm: 1 },
          replicas_consistent: true,
          matches_image: null,
        },
      ]);
    mocks.getServiceComponents.mockReset().mockResolvedValue({
      ...SUMMARY,
      components: [
        {
          name: "urllib3",
          version: "2.2.1",
          ecosystem: "PyPI",
          purl: "pkg:pypi/urllib3@2.2.1",
          own: false,
        },
        {
          name: "openssl",
          version: "3.5.5",
          ecosystem: "Debian",
          purl: "pkg:deb/openssl@3.5.5",
          own: false,
        },
      ],
    });
    mocks.searchComponents
      .mockReset()
      .mockResolvedValue([
        {
          service: "document-service",
          name: "urllib3",
          version: "2.2.1",
          ecosystem: "PyPI",
          purl: "x",
        },
      ]);
    mocks.exportComponents.mockReset().mockResolvedValue(new Blob(["{}"]));
  });

  it("lists services and flags differing replicas", async () => {
    renderPage();
    const row = (await screen.findByText("document-service")).closest("tr")!;
    expect(within(row).getByText("nein")).toBeInTheDocument();
    expect(screen.getByText("Oberfläche")).toBeInTheDocument();
  });

  it("opens a service's components and filters them", async () => {
    renderPage();
    const row = (await screen.findByText("document-service")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Anzeigen" }));
    expect(await screen.findByText("openssl")).toBeInTheDocument();
    expect(screen.getByText(/unterschiedlichen Paketständen/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filtern"), { target: { value: "ssl" } });
    expect(screen.queryByText("urllib3")).not.toBeInTheDocument();
  });

  it("searches across services", async () => {
    renderPage();
    await screen.findByText("document-service");
    fireEvent.change(screen.getByLabelText("Paket suchen", { selector: "input" }), {
      target: { value: "urllib3" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Paket suchen" })[0]);
    await waitFor(() => expect(mocks.searchComponents).toHaveBeenCalledWith("tok", "urllib3"));
    expect(await screen.findByText("2.2.1")).toBeInTheDocument();
  });
});
