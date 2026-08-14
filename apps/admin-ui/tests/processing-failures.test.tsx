import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessingFailuresView } from "@/components/ProcessingFailuresView";
import { I18nProvider } from "@/i18n";

function renderView() {
  return render(
    <I18nProvider>
      <ProcessingFailuresView />
    </I18nProvider>
  );
}

const listNotificationsMock = vi.fn();
const retryNotificationMock = vi.fn();
const listRenditionsMock = vi.fn();
const retryRenditionMock = vi.fn();
const listOcrResultsMock = vi.fn();
const retryOcrResultMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listNotifications: (...args: unknown[]) => listNotificationsMock(...args),
  retryNotification: (...args: unknown[]) => retryNotificationMock(...args),
  listRenditions: (...args: unknown[]) => listRenditionsMock(...args),
  retryRendition: (...args: unknown[]) => retryRenditionMock(...args),
  listOcrResults: (...args: unknown[]) => listOcrResultsMock(...args),
  retryOcrResult: (...args: unknown[]) => retryOcrResultMock(...args),
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

const FAILED_NOTIFICATION = {
  id: "n1",
  channel: "webhook",
  recipient: "http://example.test/hook",
  subject: "S",
  body: "B",
  status: "failed_permanent",
  error: "connection refused",
  attempts: 5,
  next_retry_at: null,
  created_at: "2026-01-01T00:00:00Z",
  sent_at: null,
};

const FAILED_RENDITION = {
  id: "doc-1:1:thumbnail",
  document_id: "doc-1",
  version_number: 1,
  rendition_type: "thumbnail",
  status: "failed_permanent",
  error_message: "renderer crashed",
  attempts: 5,
  next_retry_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const FAILED_OCR_RESULT = {
  id: "doc-2:1",
  document_id: "doc-2",
  version_number: 1,
  status: "failed_permanent",
  error_message: "tesseract crashed",
  attempts: 5,
  next_retry_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("ProcessingFailuresView", () => {
  beforeEach(() => {
    listNotificationsMock.mockReset();
    retryNotificationMock.mockReset();
    listRenditionsMock.mockReset();
    retryRenditionMock.mockReset();
    listOcrResultsMock.mockReset();
    retryOcrResultMock.mockReset();
  });

  it("lists failed_permanent items from all three services with the status filter", async () => {
    listNotificationsMock.mockResolvedValue([FAILED_NOTIFICATION]);
    listRenditionsMock.mockResolvedValue([FAILED_RENDITION]);
    listOcrResultsMock.mockResolvedValue([FAILED_OCR_RESULT]);

    renderView();

    expect(await screen.findByText("webhook")).toBeInTheDocument();
    expect(screen.getByText("thumbnail")).toBeInTheDocument();
    expect(screen.getByText("doc-2")).toBeInTheDocument();
    expect(listNotificationsMock).toHaveBeenCalledWith("token-123", "failed_permanent");
    expect(listRenditionsMock).toHaveBeenCalledWith("token-123", "failed_permanent");
    expect(listOcrResultsMock).toHaveBeenCalledWith("token-123", "failed_permanent");
  });

  it("shows empty states per section without any failures", async () => {
    listNotificationsMock.mockResolvedValue([]);
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock.mockResolvedValue([]);

    renderView();

    expect(await screen.findByText("Keine dauerhaft fehlgeschlagenen Benachrichtigungen.")).toBeInTheDocument();
    expect(screen.getByText("Keine dauerhaft fehlgeschlagenen Ersatzdarstellungen.")).toBeInTheDocument();
    expect(screen.getByText("Keine dauerhaft fehlgeschlagenen OCR-Ergebnisse.")).toBeInTheDocument();
  });

  it("shows an unreachable state when notification-service cannot be reached", async () => {
    listNotificationsMock.mockRejectedValue(new TypeError("Failed to fetch"));
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock.mockResolvedValue([]);

    renderView();

    expect(await screen.findByText(/Notification Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("retries a failed notification and reloads", async () => {
    listNotificationsMock
      .mockResolvedValueOnce([FAILED_NOTIFICATION])
      .mockResolvedValueOnce([{ ...FAILED_NOTIFICATION, status: "failed" }]);
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock.mockResolvedValue([]);
    retryNotificationMock.mockResolvedValue({ ...FAILED_NOTIFICATION, status: "failed" });

    renderView();
    await screen.findByText("webhook");

    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));

    expect(retryNotificationMock).toHaveBeenCalledWith("token-123", "n1");
    await vi.waitFor(() => expect(listNotificationsMock).toHaveBeenCalledTimes(2));
  });

  it("retries a failed rendition and reloads", async () => {
    listNotificationsMock.mockResolvedValue([]);
    listRenditionsMock
      .mockResolvedValueOnce([FAILED_RENDITION])
      .mockResolvedValueOnce([{ ...FAILED_RENDITION, status: "failed" }]);
    listOcrResultsMock.mockResolvedValue([]);
    retryRenditionMock.mockResolvedValue({ ...FAILED_RENDITION, status: "failed" });

    renderView();
    await screen.findByText("thumbnail");

    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));

    expect(retryRenditionMock).toHaveBeenCalledWith("token-123", "doc-1:1:thumbnail");
    await vi.waitFor(() => expect(listRenditionsMock).toHaveBeenCalledTimes(2));
  });

  it("retries a failed OCR result and reloads", async () => {
    listNotificationsMock.mockResolvedValue([]);
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock
      .mockResolvedValueOnce([FAILED_OCR_RESULT])
      .mockResolvedValueOnce([{ ...FAILED_OCR_RESULT, status: "failed" }]);
    retryOcrResultMock.mockResolvedValue({ ...FAILED_OCR_RESULT, status: "failed" });

    renderView();
    await screen.findByText("doc-2");

    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));

    expect(retryOcrResultMock).toHaveBeenCalledWith("token-123", "doc-2:1");
    await vi.waitFor(() => expect(listOcrResultsMock).toHaveBeenCalledTimes(2));
  });
});
