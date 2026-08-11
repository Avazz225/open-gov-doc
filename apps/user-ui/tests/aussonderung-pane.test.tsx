import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AussonderungPane } from "@/components/AussonderungPane";
import { I18nProvider } from "@/i18n";

const listReleasedItemsMock = vi.fn();
const retrieveArchivalTransferMock = vi.fn();
const downloadCaseArchivalPackageMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listReleasedItems: (...args: unknown[]) => listReleasedItemsMock(...args),
    retrieveArchivalTransfer: (...args: unknown[]) => retrieveArchivalTransferMock(...args),
    downloadCaseArchivalPackage: (...args: unknown[]) => downloadCaseArchivalPackageMock(...args),
  };
});

function renderPane() {
  return render(
    <I18nProvider>
      <AussonderungPane token="token-123" />
    </I18nProvider>
  );
}

const documentItem = {
  transfer_id: "transfer-doc-1",
  kind: "document" as const,
  subject_id: "doc-1",
  title: "Rueckmeldung Buergeranfrage",
  identifier: "2026-042",
  released_at: new Date("2026-01-01T00:00:00Z").toISOString(),
  purge_at: new Date("2026-01-31T00:00:00Z").toISOString(),
};

const caseItem = {
  transfer_id: "transfer-case-1",
  kind: "case" as const,
  subject_id: "case-1",
  title: "Bauantrag Musterstrasse",
  identifier: "2026-007",
  released_at: new Date("2026-01-02T00:00:00Z").toISOString(),
  purge_at: null,
};

describe("AussonderungPane", () => {
  beforeEach(() => {
    listReleasedItemsMock.mockReset();
    retrieveArchivalTransferMock.mockReset();
    downloadCaseArchivalPackageMock.mockReset();
    listReleasedItemsMock.mockResolvedValue([]);
  });

  it("shows the empty state when there are no released items", async () => {
    renderPane();
    expect(
      await screen.findByText("Keine ausgesonderten Elemente innerhalb der Übergangsfrist.")
    ).toBeInTheDocument();
  });

  it("lists released documents and cases with identifier and dates", async () => {
    listReleasedItemsMock.mockResolvedValue([documentItem, caseItem]);

    renderPane();

    expect(await screen.findByText(/Rueckmeldung Buergeranfrage/)).toBeInTheDocument();
    expect(screen.getByText(/2026-042/)).toBeInTheDocument();
    expect(screen.getByText(/Bauantrag Musterstrasse/)).toBeInTheDocument();
    expect(screen.getByText(/2026-007/)).toBeInTheDocument();
  });

  it("searches with the entered query", async () => {
    listReleasedItemsMock.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPane();

    await waitFor(() => expect(listReleasedItemsMock).toHaveBeenCalledWith("token-123", undefined));

    await user.type(screen.getByLabelText("Suche"), "2026-042");
    await user.click(screen.getByText("Suchen"));

    await waitFor(() =>
      expect(listReleasedItemsMock).toHaveBeenCalledWith("token-123", "2026-042")
    );
  });

  it("retrieves a document and reloads the list", async () => {
    listReleasedItemsMock
      .mockResolvedValueOnce([documentItem])
      .mockResolvedValueOnce([{ ...documentItem, released_at: new Date().toISOString() }]);
    retrieveArchivalTransferMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Rueckmeldung Buergeranfrage/);
    await user.click(screen.getByText("Rückholung"));

    await waitFor(() =>
      expect(retrieveArchivalTransferMock).toHaveBeenCalledWith("token-123", "transfer-doc-1")
    );
    await waitFor(() => expect(listReleasedItemsMock).toHaveBeenCalledTimes(2));
  });

  it("shows an error when retrieval fails", async () => {
    listReleasedItemsMock.mockResolvedValue([documentItem]);
    retrieveArchivalTransferMock.mockRejectedValue(new Error("boom"));

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Rueckmeldung Buergeranfrage/);
    await user.click(screen.getByText("Rückholung"));

    expect(await screen.findByText("Rückholung fehlgeschlagen")).toBeInTheDocument();
  });

  it("downloads a case package", async () => {
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    listReleasedItemsMock.mockResolvedValue([caseItem]);
    downloadCaseArchivalPackageMock.mockResolvedValue(new Blob(["PK"]));

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Bauantrag Musterstrasse/);
    await user.click(screen.getByText("Paket herunterladen"));

    await waitFor(() =>
      expect(downloadCaseArchivalPackageMock).toHaveBeenCalledWith("token-123", "transfer-case-1")
    );
  });
});
