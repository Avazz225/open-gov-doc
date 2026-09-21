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
const listHandoversMock = vi.fn();
const retryHandoverMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listNotifications: (...args: unknown[]) => listNotificationsMock(...args),
  retryNotification: (...args: unknown[]) => retryNotificationMock(...args),
  listRenditions: (...args: unknown[]) => listRenditionsMock(...args),
  retryRendition: (...args: unknown[]) => retryRenditionMock(...args),
  listOcrResults: (...args: unknown[]) => listOcrResultsMock(...args),
  retryOcrResult: (...args: unknown[]) => retryOcrResultMock(...args),
  listHandovers: (...args: unknown[]) => listHandoversMock(...args),
  retryHandover: (...args: unknown[]) => retryHandoverMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

// P63-S3: notification-service is the only one of the four sections gated by
// a real capability (`admin.notification_read`) - default to holding it so
// the existing tests below keep exercising the notification section's normal
// behavior; the dedicated "missing permission" test overrides this to [].
let mockPermissions: string[] = ["admin.notification_read"];

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
      permissions: mockPermissions,
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

const FAILED_HANDOVER_FORWARD = {
  id: "handover-1",
  from_installation_id: "install-a",
  to_installation_id: "install-b",
  process_type: "test-process",
  status: "delivery_failed",
  attempts: 5,
  next_retry_at: null,
  result_attempts: 0,
  result_next_retry_at: null,
  created_at: "2026-01-01T00:00:00Z",
  delivered_at: null,
  completed_at: null,
};

const FAILED_HANDOVER_RESULT = {
  id: "handover-2",
  from_installation_id: "install-c",
  to_installation_id: "install-d",
  process_type: "test-process",
  status: "result_delivery_failed",
  attempts: 0,
  next_retry_at: null,
  result_attempts: 5,
  result_next_retry_at: null,
  created_at: "2026-01-01T00:00:00Z",
  delivered_at: "2026-01-01T00:00:01Z",
  completed_at: "2026-01-01T00:00:02Z",
};

function mockHandoverLists(byStatus: Record<string, unknown[]>) {
  listHandoversMock.mockImplementation(async (status: string) => byStatus[status] ?? []);
}

describe("ProcessingFailuresView", () => {
  beforeEach(() => {
    mockPermissions = ["admin.notification_read"];
    listNotificationsMock.mockReset();
    retryNotificationMock.mockReset();
    listRenditionsMock.mockReset();
    retryRenditionMock.mockReset();
    listOcrResultsMock.mockReset();
    retryOcrResultMock.mockReset();
    listHandoversMock.mockReset();
    retryHandoverMock.mockReset();
    mockHandoverLists({});
  });

  it("lists failed_permanent items from all three gateway-routed services with the status filter", async () => {
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
    expect(screen.getByText("Keine dauerhaft fehlgeschlagenen Handover-Vermittlungen.")).toBeInTheDocument();
  });

  it("shows a missing-permission state for notifications without calling the API, while the other three sections still load", async () => {
    mockPermissions = [];
    listRenditionsMock.mockResolvedValue([FAILED_RENDITION]);
    listOcrResultsMock.mockResolvedValue([FAILED_OCR_RESULT]);

    renderView();

    expect(
      await screen.findByText("Keine Berechtigung, um fehlgeschlagene Benachrichtigungen einzusehen.")
    ).toBeInTheDocument();
    expect(screen.getByText("thumbnail")).toBeInTheDocument();
    expect(listNotificationsMock).not.toHaveBeenCalled();
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

  it("lists failed handovers from BOTH legs via listHandovers, called directly (no gateway token)", async () => {
    listNotificationsMock.mockResolvedValue([]);
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock.mockResolvedValue([]);
    mockHandoverLists({
      delivery_failed: [FAILED_HANDOVER_FORWARD],
      result_delivery_failed: [FAILED_HANDOVER_RESULT],
    });

    renderView();

    expect(await screen.findByText("install-a")).toBeInTheDocument();
    expect(screen.getByText("install-d")).toBeInTheDocument();
    expect(screen.getByText("Hinweg (an Ziel)")).toBeInTheDocument();
    expect(screen.getByText("Rückweg (Ergebnis an Absender)")).toBeInTheDocument();
    expect(listHandoversMock).toHaveBeenCalledWith("delivery_failed");
    expect(listHandoversMock).toHaveBeenCalledWith("result_delivery_failed");
    // Direct call, not routed through the gateway/token like the other
    // three sections above (Phase 40 Session 3 - federation-hub-service
    // isn't proxyable, see lib/api.ts).
    expect(listHandoversMock).not.toHaveBeenCalledWith("token-123", expect.anything());
  });

  it("shows an unreachable state when federation-hub-service cannot be reached", async () => {
    listNotificationsMock.mockResolvedValue([]);
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock.mockResolvedValue([]);
    listHandoversMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderView();

    expect(await screen.findByText(/Federation Hub nicht erreichbar/)).toBeInTheDocument();
  });

  it("retries a failed handover (result leg) and reloads", async () => {
    listNotificationsMock.mockResolvedValue([]);
    listRenditionsMock.mockResolvedValue([]);
    listOcrResultsMock.mockResolvedValue([]);
    listHandoversMock
      .mockImplementationOnce(async (status: string) =>
        status === "result_delivery_failed" ? [FAILED_HANDOVER_RESULT] : []
      )
      .mockImplementationOnce(async (status: string) =>
        status === "result_delivery_failed" ? [{ ...FAILED_HANDOVER_RESULT, status: "completed" }] : []
      );
    retryHandoverMock.mockResolvedValue({ ...FAILED_HANDOVER_RESULT, status: "completed" });

    renderView();
    await screen.findByText("install-c");

    // ADR 0162/Phase 44 Session 1: retry is gated by an operator-typed key,
    // the retry button stays disabled (`operatorKey.length === 0`) until one
    // is entered - this test pre-dates that change and was left stale
    // (asserting a single-arg `retryHandover` call that could never have
    // fired, since the button was never actually clickable). Fixed here as
    // an incidental discovery while getting the frontend regression green
    // for P63-S3, unrelated to that session's own scope.
    fireEvent.change(
      screen.getByPlaceholderText("Erforderlich für 'Erneut versuchen' (Phase 44 Session 1, ADR 0162)"),
      { target: { value: "test-operator-key" } }
    );
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));

    expect(retryHandoverMock).toHaveBeenCalledWith("handover-2", "test-operator-key");
    await vi.waitFor(() => expect(listHandoversMock).toHaveBeenCalledTimes(4));
  });
});
