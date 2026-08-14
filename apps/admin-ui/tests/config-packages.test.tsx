import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigPackages } from "@/components/ConfigPackages";
import { I18nProvider } from "@/i18n";

function renderConfigPackages() {
  return render(
    <I18nProvider>
      <ConfigPackages />
    </I18nProvider>
  );
}

const exportConfigMock = vi.fn();
const compareConfigMock = vi.fn();
const importConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  CONFIG_CATEGORIES: [
    "object_types",
    "workflows",
    "dmn_definitions",
    "business_calendars",
    "roles",
    "approval_config",
    "sensor_config",
    "federation_config",
    "realm_roles",
  ],
  exportConfig: (...args: unknown[]) => exportConfigMock(...args),
  compareConfig: (...args: unknown[]) => compareConfigMock(...args),
  importConfig: (...args: unknown[]) => importConfigMock(...args),
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
      permissions: [],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

function makePackageFile(overrides: Record<string, unknown> = {}) {
  const doc = {
    schema_version: "1.0",
    exported_at: "2026-01-01T00:00:00Z",
    manifest: {
      name: "eGov-Konfigurationspaket",
      version: "1.0.0",
      compatibility_range: ">=1.0,<2.0",
      description: "Standardkonfiguration für die deutsche öffentliche Verwaltung",
    },
    roles: [{ name: "registratur", description: "d", permissions: ["read"] }],
    ...overrides,
  };
  return new File([JSON.stringify(doc)], "egov-paket.json", { type: "application/json" });
}

describe("ConfigPackages", () => {
  beforeEach(() => {
    exportConfigMock.mockReset();
    compareConfigMock.mockReset();
    importConfigMock.mockReset();
  });

  it("shows the intro hint", () => {
    renderConfigPackages();
    expect(screen.getByText(/kuratiertes, versioniertes Konfigurationsdokument/)).toBeInTheDocument();
  });

  it("loads a package file and shows its manifest and categories", async () => {
    const user = userEvent.setup();
    renderConfigPackages();

    const input = screen.getByLabelText("Paket-Datei auswählen (JSON)");
    await user.upload(input, makePackageFile());

    expect(await screen.findByText("eGov-Konfigurationspaket")).toBeInTheDocument();
    expect(screen.getByText(/1.0.0/)).toBeInTheDocument();
    expect(screen.getByText("roles (1)")).toBeInTheDocument();
  });

  it("shows an error for an invalid file", async () => {
    const user = userEvent.setup();
    renderConfigPackages();

    const input = screen.getByLabelText("Paket-Datei auswählen (JSON)");
    const badFile = new File(["not json"], "kaputt.json", { type: "application/json" });
    await user.upload(input, badFile);

    expect(await screen.findByText(/Keine gültige Konfigurationsdatei/)).toBeInTheDocument();
  });

  it("shows a hint for a document without a manifest", async () => {
    const user = userEvent.setup();
    renderConfigPackages();

    const input = screen.getByLabelText("Paket-Datei auswählen (JSON)");
    await user.upload(input, makePackageFile({ manifest: undefined }));

    expect(await screen.findByText(/Kein Manifest/)).toBeInTheDocument();
  });

  it("previews the delta against the live system via compare", async () => {
    compareConfigMock.mockResolvedValue({
      schema_version: "1.0",
      base_exported_at: "2026-01-01T00:00:00Z",
      compare_exported_at: "2026-01-01T00:00:00Z",
      categories: {
        roles: {
          only_in_base: [],
          only_in_compare: ["registratur"],
          differing: {},
          identical: [],
        },
      },
    });

    const user = userEvent.setup();
    renderConfigPackages();
    await user.upload(screen.getByLabelText("Paket-Datei auswählen (JSON)"), makePackageFile());
    await screen.findByText("eGov-Konfigurationspaket");

    await user.click(screen.getByRole("button", { name: "Vorschau" }));

    expect(await screen.findByText("Vorschau: Was würde sich ändern?")).toBeInTheDocument();
    expect(screen.getByText("registratur")).toBeInTheDocument();
    expect(compareConfigMock).toHaveBeenCalledWith(
      "token-123",
      expect.objectContaining({ manifest: expect.objectContaining({ name: "eGov-Konfigurationspaket" }) }),
      ["roles"]
    );
  });

  it("applies the package and shows the per-category result", async () => {
    // Since P17-S3 (14.2 "Configuration Import"), `importConfig` returns the
    // gated `ConfigImportActionResult` - without approval requirement
    // enabled (the default), `result` is set immediately.
    importConfigMock.mockResolvedValue({
      status: "applied",
      result: {
        schema_version: "1.0",
        results: {
          roles: { created: 1, updated: 0, skipped: 0, errors: [] },
        },
      },
      approval_request_id: null,
    });

    const user = userEvent.setup();
    renderConfigPackages();
    await user.upload(screen.getByLabelText("Paket-Datei auswählen (JSON)"), makePackageFile());
    await screen.findByText("eGov-Konfigurationspaket");

    await user.click(screen.getByRole("button", { name: "Paket anwenden" }));

    expect(await screen.findByText("Ergebnis der Anwendung")).toBeInTheDocument();
    expect(importConfigMock).toHaveBeenCalledWith(
      "token-123",
      expect.objectContaining({ schema_version: "1.0" })
    );
  });

  it("shows a pending-approval hint without a result table when four-eyes is active", async () => {
    importConfigMock.mockResolvedValue({
      status: "pending_approval",
      result: null,
      approval_request_id: "req-1",
    });

    const user = userEvent.setup();
    renderConfigPackages();
    await user.upload(screen.getByLabelText("Paket-Datei auswählen (JSON)"), makePackageFile());
    await screen.findByText("eGov-Konfigurationspaket");

    await user.click(screen.getByRole("button", { name: "Paket anwenden" }));

    expect(
      await screen.findByText(
        "Vier-Augen-Prinzip aktiv - die Anwendung des Pakets wartet auf Genehmigung durch eine zweite Person, bevor sie erfolgt."
      )
    ).toBeInTheDocument();
    expect(screen.queryByText("Ergebnis der Anwendung")).not.toBeInTheDocument();
  });

  it("exports the current live configuration", async () => {
    exportConfigMock.mockResolvedValue({ schema_version: "1.0", exported_at: "2026-01-01T00:00:00Z" });
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();

    const user = userEvent.setup();
    renderConfigPackages();
    await user.click(screen.getByRole("button", { name: "Aktuelle Konfiguration exportieren" }));

    await waitFor(() => expect(exportConfigMock).toHaveBeenCalledWith("token-123"));
  });
});
