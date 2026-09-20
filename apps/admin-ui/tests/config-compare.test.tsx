import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigCompare } from "@/components/ConfigCompare";
import { I18nProvider } from "@/i18n";
import { InstallationProvider } from "@/lib/installation-context";

function renderConfigCompare() {
  return render(
    <I18nProvider>
      <InstallationProvider>
        <ConfigCompare />
      </InstallationProvider>
    </I18nProvider>
  );
}

const loginAtMock = vi.fn();
const exportConfigAtMock = vi.fn();
const compareConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  // `InstallationProvider` calls this on every render (see its own
  // module docstring) - a no-op stub is enough, this test never asserts
  // on the active installation's own `gatewayBaseUrl`.
  setGatewayBaseUrl: vi.fn(),
  loginAt: (...args: unknown[]) => loginAtMock(...args),
  exportConfigAt: (...args: unknown[]) => exportConfigAtMock(...args),
  compareConfig: (...args: unknown[]) => compareConfigMock(...args),
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
      user: { sub: "admin-1", username: "admin", email: null, realm_roles: [] },
      permissions: [],
      accessToken: "active-token",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const OTHER_INSTALLATION = { id: "prod", name: "Produktion", gatewayBaseUrl: "https://prod.example.org" };

function seedTwoInstallations() {
  window.localStorage.setItem(
    "dms.installations",
    JSON.stringify([{ id: "default", name: "Lokal", gatewayBaseUrl: "http://localhost:8000" }, OTHER_INSTALLATION])
  );
  window.localStorage.setItem("dms.activeInstallationId", "default");
}

const COMPARE_RESULT = {
  schema_version: "1",
  base_exported_at: "2026-01-01T00:00:00Z",
  compare_exported_at: "2026-01-02T00:00:00Z",
  categories: {
    object_types: {
      only_in_base: ["Rechnung"],
      only_in_compare: ["Vertrag"],
      differing: { Akte: { name: { base: "Akte", compare: "Akte (neu)" } } },
      identical: [],
    },
  },
};

describe("ConfigCompare", () => {
  beforeEach(() => {
    window.localStorage.clear();
    loginAtMock.mockReset();
    exportConfigAtMock.mockReset();
    compareConfigMock.mockReset();
  });

  it("shows a hint instead of a form when no other installation is known", () => {
    renderConfigCompare();

    expect(
      screen.getByText(
        'Keine weitere Installation bekannt - unter "Installationsverwaltung" zunächst eine zweite Installation hinzufügen.'
      )
    ).toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("logs into the other installation, exports its config, and compares against the active one's own live export", async () => {
    seedTwoInstallations();
    loginAtMock.mockResolvedValue({
      access_token: "other-token",
      refresh_token: "r",
      expires_in: 3600,
      token_type: "bearer",
    });
    const otherExport = { schema_version: "1", exported_at: "2026-01-02T00:00:00Z" };
    exportConfigAtMock.mockResolvedValue(otherExport);
    compareConfigMock.mockResolvedValue(COMPARE_RESULT);

    renderConfigCompare();

    const form = await screen.findByRole("form", { name: "Vergleich starten" });
    fireEvent.change(within(form).getByLabelText("Zu vergleichende Installation"), {
      target: { value: "prod" },
    });
    fireEvent.change(within(form).getByLabelText("Benutzername (dort)"), {
      target: { value: "users-admin" },
    });
    fireEvent.change(within(form).getByLabelText("Passwort (dort)"), {
      target: { value: "secret" },
    });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(loginAtMock).toHaveBeenCalledWith(
        "https://prod.example.org",
        "users-admin",
        "secret"
      )
    );
    expect(exportConfigAtMock).toHaveBeenCalledWith("https://prod.example.org", "other-token");
    expect(compareConfigMock).toHaveBeenCalledWith("active-token", otherExport);

    expect(await screen.findByText("Rechnung")).toBeInTheDocument();
    expect(screen.getByText("Vertrag")).toBeInTheDocument();
    expect(screen.getByText("Akte")).toBeInTheDocument();
  });

  it("shows an error when the login against the other installation fails", async () => {
    seedTwoInstallations();
    loginAtMock.mockRejectedValue(new Error("invalid credentials"));

    renderConfigCompare();

    const form = await screen.findByRole("form", { name: "Vergleich starten" });
    fireEvent.change(within(form).getByLabelText("Zu vergleichende Installation"), {
      target: { value: "prod" },
    });
    fireEvent.change(within(form).getByLabelText("Benutzername (dort)"), {
      target: { value: "users-admin" },
    });
    fireEvent.change(within(form).getByLabelText("Passwort (dort)"), {
      target: { value: "wrong" },
    });
    fireEvent.submit(form);

    expect(
      await screen.findByText(
        "Vergleich fehlgeschlagen - Anmeldedaten oder Erreichbarkeit der anderen Installation prüfen."
      )
    ).toBeInTheDocument();
    expect(exportConfigAtMock).not.toHaveBeenCalled();
    expect(compareConfigMock).not.toHaveBeenCalled();
  });
});
