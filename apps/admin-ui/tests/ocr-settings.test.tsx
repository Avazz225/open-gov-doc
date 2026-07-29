import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OcrSettings } from "@/components/OcrSettings";
import { I18nProvider } from "@/i18n";

function renderOcrSettings() {
  return render(
    <I18nProvider>
      <OcrSettings />
    </I18nProvider>
  );
}

const getOcrConfigMock = vi.fn();
const updateOcrConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getOcrConfig: (...args: unknown[]) => getOcrConfigMock(...args),
  updateOcrConfig: (...args: unknown[]) => updateOcrConfigMock(...args),
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
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("OcrSettings", () => {
  beforeEach(() => {
    getOcrConfigMock.mockReset();
    updateOcrConfigMock.mockReset();
  });

  it("loads and shows the current configuration", async () => {
    getOcrConfigMock.mockResolvedValue({
      max_word_count: 5000,
      batch_size: 4,
      allowed_content_types: ["application/pdf"],
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderOcrSettings();

    expect(await screen.findByDisplayValue("5000")).toBeInTheDocument();
    expect(screen.getByDisplayValue("4")).toBeInTheDocument();
    expect(screen.getByDisplayValue("application/pdf")).toBeInTheDocument();
  });

  it("shows an unreachable state when ocr-service is not deployed (ocrEnabled=false)", async () => {
    getOcrConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderOcrSettings();

    expect(
      await screen.findByText(/OCR Service nicht erreichbar/)
    ).toBeInTheDocument();
  });

  it("saves changed values", async () => {
    getOcrConfigMock.mockResolvedValue({
      max_word_count: null,
      batch_size: 4,
      allowed_content_types: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateOcrConfigMock.mockResolvedValue({
      max_word_count: 3000,
      batch_size: 8,
      allowed_content_types: ["application/pdf", "image/png"],
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderOcrSettings();
    await screen.findByDisplayValue("4");

    fireEvent.change(screen.getByLabelText("Maximale Wortobergrenze"), {
      target: { value: "3000" },
    });
    fireEvent.change(screen.getByLabelText("Verarbeitungs-Batch-Size"), {
      target: { value: "8" },
    });
    fireEvent.change(screen.getByLabelText("Für OCR erlaubte Content-Types"), {
      target: { value: "application/pdf, image/png" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateOcrConfigMock).toHaveBeenCalledWith("token-123", {
      maxWordCount: 3000,
      batchSize: 8,
      allowedContentTypes: ["application/pdf", "image/png"],
    });
  });
});
