import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ForensicTraceView } from "@/components/ForensicTraceView";
import { I18nProvider } from "@/i18n";

function renderForensicTraceView() {
  return render(
    <I18nProvider>
      <ForensicTraceView />
    </I18nProvider>
  );
}

const getForensicTraceMock = vi.fn();
const exportForensicTraceMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getForensicTrace: (...args: unknown[]) => getForensicTraceMock(...args),
  exportForensicTrace: (...args: unknown[]) => exportForensicTraceMock(...args),
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
      user: { sub: "u1", username: "security-officer", email: null, realm_roles: [] },
      permissions: [],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("ForensicTraceView", () => {
  beforeEach(() => {
    getForensicTraceMock.mockReset();
    exportForensicTraceMock.mockReset();
  });

  it("shows a hint before any query has been run", () => {
    renderForensicTraceView();

    expect(screen.getByText("Noch keine Abfrage gestartet.")).toBeInTheDocument();
    expect(getForensicTraceMock).not.toHaveBeenCalled();
  });

  it("queries the trace with the current user as queried_by and renders results", async () => {
    getForensicTraceMock.mockResolvedValue({
      entries: [
        {
          id: 1,
          event_type: "document.downloaded",
          category: "download",
          occurred_at: "2026-08-01T10:00:00Z",
          service_name: "document-service",
          subject: "doc-1",
          actor: "alice",
          payload: { version_number: 1 },
        },
      ],
      anomalies: [],
    });
    const user = userEvent.setup();

    renderForensicTraceView();

    await user.type(screen.getByPlaceholderText("Nach Nutzer filtern (Akteur)"), "alice");
    await user.click(screen.getByText("Trace abfragen"));

    await waitFor(() => expect(getForensicTraceMock).toHaveBeenCalled());
    expect(getForensicTraceMock).toHaveBeenCalledWith(
      "token-123",
      "security-officer",
      expect.objectContaining({ actor: "alice" })
    );

    expect(await screen.findByText("document.downloaded")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("doc-1")).toBeInTheDocument();
  });

  it("shows an empty state when the query returns no entries", async () => {
    getForensicTraceMock.mockResolvedValue({ entries: [], anomalies: [] });
    const user = userEvent.setup();

    renderForensicTraceView();
    await user.click(screen.getByText("Trace abfragen"));

    expect(await screen.findByText("Keine Treffer für die gewählten Filter.")).toBeInTheDocument();
  });

  it("renders anomaly hints when present", async () => {
    getForensicTraceMock.mockResolvedValue({
      entries: [],
      anomalies: ["Nutzer 'alice' hat 42 Downloads innerhalb von 5 Minuten (Schwellwert: 20)."],
    });
    const user = userEvent.setup();

    renderForensicTraceView();
    await user.click(screen.getByText("Trace abfragen"));

    expect(await screen.findByText("Anomalie-Hinweise")).toBeInTheDocument();
    expect(
      screen.getByText("Nutzer 'alice' hat 42 Downloads innerhalb von 5 Minuten (Schwellwert: 20).")
    ).toBeInTheDocument();
  });

  it("shows an error message when the query fails", async () => {
    getForensicTraceMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();

    renderForensicTraceView();
    await user.click(screen.getByText("Trace abfragen"));

    expect(await screen.findByText("Trace konnte nicht geladen werden")).toBeInTheDocument();
  });

  it("triggers a CSV export with the current filters and queried_by", async () => {
    const blob = new Blob(["a,b"], { type: "text/csv" });
    exportForensicTraceMock.mockResolvedValue(blob);
    const user = userEvent.setup();
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    renderForensicTraceView();
    await user.type(screen.getByPlaceholderText("Nach Objekt-ID filtern (Dokument/Ordner)"), "doc-1");
    await user.click(screen.getByText("CSV-Export"));

    await waitFor(() => expect(exportForensicTraceMock).toHaveBeenCalled());
    expect(exportForensicTraceMock).toHaveBeenCalledWith(
      "token-123",
      "security-officer",
      "csv",
      expect.objectContaining({ subject: "doc-1" })
    );
    expect(createObjectURL).toHaveBeenCalledWith(blob);

    vi.unstubAllGlobals();
  });
});
