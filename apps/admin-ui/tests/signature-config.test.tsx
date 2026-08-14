import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignatureConfig } from "@/components/SignatureConfig";
import { I18nProvider } from "@/i18n";

function renderSignatureConfig() {
  return render(
    <I18nProvider>
      <SignatureConfig />
    </I18nProvider>
  );
}

const getSignatureConfigMock = vi.fn();
const updateSignatureConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getSignatureConfig: (...args: unknown[]) => getSignatureConfigMock(...args),
  updateSignatureConfig: (...args: unknown[]) => updateSignatureConfigMock(...args),
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

const INTERNAL_PROVIDER = { id: "internal", type: "internal" as const, levels: ["ses", "aes"] };

describe("SignatureConfig", () => {
  beforeEach(() => {
    getSignatureConfigMock.mockReset();
    updateSignatureConfigMock.mockReset();
  });

  it("lists configured connectors with their levels", async () => {
    getSignatureConfigMock.mockResolvedValue([INTERNAL_PROVIDER]);

    renderSignatureConfig();

    await screen.findAllByText("internal");
    const row = screen.getAllByText("internal")[0].closest("tr")!;
    expect(within(row).getByRole("checkbox", { name: "SES" })).toBeChecked();
    expect(within(row).getByRole("checkbox", { name: "AES" })).toBeChecked();
    expect(within(row).getByRole("checkbox", { name: "QES" })).not.toBeChecked();
  });

  it("shows an empty state without any connectors", async () => {
    getSignatureConfigMock.mockResolvedValue([]);

    renderSignatureConfig();

    expect(await screen.findByText("Keine Connectoren konfiguriert.")).toBeInTheDocument();
  });

  it("shows an unreachable state when signature-service is not reachable", async () => {
    getSignatureConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderSignatureConfig();

    expect(await screen.findByText(/Signature Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("toggles a level off and reloads", async () => {
    getSignatureConfigMock
      .mockResolvedValueOnce([INTERNAL_PROVIDER])
      .mockResolvedValueOnce([{ ...INTERNAL_PROVIDER, levels: ["ses"] }]);
    updateSignatureConfigMock.mockResolvedValue([{ ...INTERNAL_PROVIDER, levels: ["ses"] }]);

    renderSignatureConfig();
    await screen.findAllByText("internal");
    const row = screen.getAllByText("internal")[0].closest("tr")!;

    fireEvent.click(within(row).getByRole("checkbox", { name: "AES" }));

    await waitFor(() =>
      expect(updateSignatureConfigMock).toHaveBeenCalledWith("token-123", [
        { id: "internal", levels: ["ses"] },
      ])
    );
    await waitFor(() => expect(getSignatureConfigMock).toHaveBeenCalledTimes(2));
  });

  it("does not allow unchecking the last remaining level", async () => {
    getSignatureConfigMock.mockResolvedValue([{ ...INTERNAL_PROVIDER, levels: ["ses"] }]);

    renderSignatureConfig();
    await screen.findAllByText("internal");
    const row = screen.getAllByText("internal")[0].closest("tr")!;

    fireEvent.click(within(row).getByRole("checkbox", { name: "SES" }));

    expect(updateSignatureConfigMock).not.toHaveBeenCalled();
  });

  it("shows an error when saving fails", async () => {
    getSignatureConfigMock.mockResolvedValue([INTERNAL_PROVIDER]);
    const { ApiError } = await import("@/lib/api");
    updateSignatureConfigMock.mockRejectedValue(
      new ApiError(422, "Connector 'internal': type=internal kann kein QES ausstellen")
    );

    renderSignatureConfig();
    await screen.findAllByText("internal");
    const row = screen.getAllByText("internal")[0].closest("tr")!;
    fireEvent.click(within(row).getByRole("checkbox", { name: "QES" }));

    expect(
      await screen.findByText("Connector 'internal': type=internal kann kein QES ausstellen")
    ).toBeInTheDocument();
  });
});
