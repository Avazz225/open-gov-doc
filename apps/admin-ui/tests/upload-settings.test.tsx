import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UploadSettings } from "@/components/UploadSettings";
import { I18nProvider } from "@/i18n";

function renderUploadSettings() {
  return render(
    <I18nProvider>
      <UploadSettings />
    </I18nProvider>
  );
}

const getUploadConfigMock = vi.fn();
const updateUploadConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getUploadConfig: (...args: unknown[]) => getUploadConfigMock(...args),
  updateUploadConfig: (...args: unknown[]) => updateUploadConfigMock(...args),
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

describe("UploadSettings", () => {
  beforeEach(() => {
    getUploadConfigMock.mockReset();
    updateUploadConfigMock.mockReset();
  });

  it("loads and shows the current whitelist", async () => {
    getUploadConfigMock.mockResolvedValue({
      allowed_content_types: ["application/pdf", "text/plain"],
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderUploadSettings();

    expect(await screen.findByDisplayValue("application/pdf, text/plain")).toBeInTheDocument();
  });

  it("shows an unreachable state when document-service is not reachable", async () => {
    getUploadConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderUploadSettings();

    expect(await screen.findByText("Document Service nicht erreichbar.")).toBeInTheDocument();
  });

  it("saves the changed whitelist", async () => {
    getUploadConfigMock.mockResolvedValue({
      allowed_content_types: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateUploadConfigMock.mockResolvedValue({
      allowed_content_types: ["application/pdf"],
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderUploadSettings();
    await screen.findByDisplayValue("");

    fireEvent.change(screen.getByLabelText("Erlaubte Content-Types"), {
      target: { value: "application/pdf" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateUploadConfigMock).toHaveBeenCalledWith("token-123", {
      allowedContentTypes: ["application/pdf"],
    });
  });
});
