import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QuarantinePane } from "@/components/QuarantinePane";
import { I18nProvider } from "@/i18n";

const listQuarantinedScansMock = vi.fn();
const releaseQuarantinedScanMock = vi.fn();
const purgeQuarantinedScanMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listQuarantinedScans: (...args: unknown[]) => listQuarantinedScansMock(...args),
    releaseQuarantinedScan: (...args: unknown[]) => releaseQuarantinedScanMock(...args),
    purgeQuarantinedScan: (...args: unknown[]) => purgeQuarantinedScanMock(...args),
  };
});

function renderPane() {
  return render(
    <I18nProvider>
      <QuarantinePane token="token-123" />
    </I18nProvider>
  );
}

const scan1 = {
  id: "scan-1",
  document_id: null,
  filename: "verdaechtig.pdf",
  content_type: "application/pdf",
  size_bytes: 42,
  checksum_sha256: "abc",
  status: "infected" as const,
  threat_name: "Eicar-Test-Signature",
  engine: "eicar",
  quarantine_object_key: "quarantine/scan-1",
  created_by: "alice",
  scanned_at: new Date().toISOString(),
  resolved_by: null,
  resolved_at: null,
};

describe("QuarantinePane", () => {
  beforeEach(() => {
    listQuarantinedScansMock.mockReset();
    releaseQuarantinedScanMock.mockReset();
    purgeQuarantinedScanMock.mockReset();
    listQuarantinedScansMock.mockResolvedValue([]);
  });

  it("shows the empty state when there are no quarantine cases", async () => {
    renderPane();
    expect(await screen.findByText("Keine Quarantäne-Fälle.")).toBeInTheDocument();
  });

  it("lists a quarantined scan with its threat name", async () => {
    listQuarantinedScansMock.mockResolvedValue([scan1]);

    renderPane();

    expect(await screen.findByText(/verdaechtig\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/Eicar-Test-Signature/)).toBeInTheDocument();
  });

  it("releases a scan after filling in the title", async () => {
    listQuarantinedScansMock.mockResolvedValueOnce([scan1]).mockResolvedValueOnce([]);
    releaseQuarantinedScanMock.mockResolvedValue({ ...scan1, status: "released" });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/verdaechtig\.pdf/);
    await user.click(screen.getByText("Freigeben"));
    await user.click(screen.getByText("Freigabe bestätigen"));

    await waitFor(() => expect(releaseQuarantinedScanMock).toHaveBeenCalled());
    expect(releaseQuarantinedScanMock).toHaveBeenCalledWith(
      "token-123",
      "scan-1",
      expect.objectContaining({ title: "verdaechtig.pdf" })
    );
  });

  it("shows an error when release fails", async () => {
    listQuarantinedScansMock.mockResolvedValue([scan1]);
    releaseQuarantinedScanMock.mockRejectedValue(new Error("boom"));

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/verdaechtig\.pdf/);
    await user.click(screen.getByText("Freigeben"));
    await user.click(screen.getByText("Freigabe bestätigen"));

    expect(await screen.findByText("Freigabe fehlgeschlagen")).toBeInTheDocument();
  });

  it("purges a scan after confirmation", async () => {
    listQuarantinedScansMock.mockResolvedValueOnce([scan1]).mockResolvedValueOnce([]);
    purgeQuarantinedScanMock.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/verdaechtig\.pdf/);
    await user.click(screen.getByText("Endgültig löschen"));

    await waitFor(() =>
      expect(purgeQuarantinedScanMock).toHaveBeenCalledWith("token-123", "scan-1")
    );
  });

  it("does not purge when the confirmation dialog is dismissed", async () => {
    listQuarantinedScansMock.mockResolvedValue([scan1]);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/verdaechtig\.pdf/);
    await user.click(screen.getByText("Endgültig löschen"));

    expect(purgeQuarantinedScanMock).not.toHaveBeenCalled();
  });
});
