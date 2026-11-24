import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IncomingSignatureSettings } from "@/components/IncomingSignatureSettings";
import { I18nProvider } from "@/i18n";

// Validating incoming signatures on the signature page (P92-S3, ADR 0248).

const api = vi.hoisted(() => ({
  getIncomingValidationConfig: vi.fn(),
  putIncomingValidationConfig: vi.fn(),
  getTrustedLists: vi.fn(),
  refreshTrustedLists: vi.fn(),
  importTrustedList: vi.fn(),
  listIncomingTrustAnchors: vi.fn(),
  addIncomingTrustAnchor: vi.fn(),
  deleteIncomingTrustAnchor: vi.fn(),
  getBackfillStatus: vi.fn(),
  startBackfill: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    ...Object.fromEntries(
      Object.keys(api).map((name) => [
        name,
        (...args: unknown[]) => (api as Record<string, (...a: unknown[]) => unknown>)[name](...args),
      ])
    ),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
      permissions: ["admin.signature_config"],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const CONFIG = {
  enabled: true,
  revocation_mode: "soft-fail",
  outbound_fetching: true,
  eu_trusted_lists: true,
  updated_at: "2026-11-24T10:00:00Z",
  updated_by: null,
};

const LISTS = {
  lists: [
    {
      kind: "lotl",
      territory: "EU",
      location_uri: "https://ec.europa.eu/tools/lotl/eu-lotl.xml",
      sequence_number: 398,
      issued_at: "2026-11-09T13:00:00Z",
      next_update: "2027-05-02T12:00:00Z",
      fetched_at: "2026-11-24T12:00:00Z",
      source: "fetch",
    },
    {
      kind: "tl",
      territory: "DE",
      location_uri: "https://tl.bundesnetzagentur.de/TL-DE.xml",
      sequence_number: 162,
      issued_at: "2026-11-01T00:00:00Z",
      next_update: "2027-04-01T00:00:00Z",
      fetched_at: "2026-11-24T12:00:00Z",
      source: "fetch",
    },
  ],
  last_fetch: {
    started_at: "2026-11-24T12:00:00Z",
    finished_at: "2026-11-24T12:02:00Z",
    source: "fetch",
    lists_stored: 30,
    errors: ["EE: ClientResponseError: 403, message='Forbidden'"],
    triggered_by: "system:schedule",
  },
  poll_interval_seconds: 86400,
  territories: [],
};

const ANCHORS = [
  {
    id: null,
    source: "internal",
    label: "Interne Signatur-CA (OG Doc)",
    subject: "CN=DMS Interne Signatur-CA",
    fingerprint_sha256: "ab",
    not_after: "2046-01-01T00:00:00Z",
    created_at: null,
    created_by: null,
  },
  {
    id: 7,
    source: "installation",
    label: "Partnerbehörde",
    subject: "CN=Partner Root",
    fingerprint_sha256: "cd",
    not_after: "2030-01-01T00:00:00Z",
    created_at: "2026-11-24T10:00:00Z",
    created_by: "admin",
  },
];

const IDLE = {
  state: "idle",
  started_at: null,
  finished_at: null,
  started_by: null,
  criteria: {},
  scanned: 0,
  queued: 0,
  skipped_validated: 0,
  limit_reached: false,
  error: null,
  queue_pending: 0,
};

function renderSettings() {
  return render(
    <I18nProvider>
      <IncomingSignatureSettings />
    </I18nProvider>
  );
}

describe("IncomingSignatureSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.getIncomingValidationConfig.mockResolvedValue(CONFIG);
    api.getTrustedLists.mockResolvedValue(LISTS);
    api.listIncomingTrustAnchors.mockResolvedValue(ANCHORS);
    api.getBackfillStatus.mockResolvedValue(IDLE);
  });

  it("saves a changed switch and the revocation mode", async () => {
    api.putIncomingValidationConfig.mockImplementation((_t: string, body: object) =>
      Promise.resolve({ ...CONFIG, ...body, updated_by: "admin" })
    );
    renderSettings();

    const fetching = await screen.findByLabelText(/Sperrlisten und Zertifikate/);
    fireEvent.click(fetching);
    await waitFor(() =>
      expect(api.putIncomingValidationConfig).toHaveBeenCalledWith("token-123", {
        enabled: true,
        revocation_mode: "soft-fail",
        outbound_fetching: false,
        eu_trusted_lists: true,
      })
    );
    fireEvent.change(screen.getByLabelText("Wenn der Sperrstatus nicht abrufbar ist"), {
      target: { value: "hard-fail" },
    });
    await waitFor(() =>
      expect(api.putIncomingValidationConfig).toHaveBeenLastCalledWith(
        "token-123",
        expect.objectContaining({ revocation_mode: "hard-fail" })
      )
    );
    expect(await screen.findAllByText("Gespeichert.")).not.toHaveLength(0);
  });

  it("shows the stored lists, the last fetch and its failures", async () => {
    renderSettings();

    expect(await screen.findByText("EU-Liste der Listen")).toBeInTheDocument();
    expect(screen.getByText("DE")).toBeInTheDocument();
    expect(screen.getByText("398")).toBeInTheDocument();
    expect(screen.getByText(/30 Liste\(n\) gespeichert \(Abruf\)/)).toBeInTheDocument();
    expect(screen.getByText("alle 24 Stunden")).toBeInTheDocument();
    expect(screen.getByText("1 Liste(n) nicht übernommen")).toBeInTheDocument();
  });

  it("fetches now and polls until the new fetch is finished", async () => {
    api.refreshTrustedLists.mockResolvedValue(undefined);
    renderSettings();
    await screen.findByText("EU-Liste der Listen");
    api.getTrustedLists.mockResolvedValue({
      ...LISTS,
      last_fetch: { ...LISTS.last_fetch, started_at: "2026-11-24T13:00:00Z", finished_at: "2026-11-24T13:02:00Z" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Jetzt abrufen" }));

    expect(await screen.findByRole("button", { name: "Rufe ab…" })).toBeDisabled();
    expect(
      await screen.findByRole("button", { name: "Jetzt abrufen" }, { timeout: 5000 })
    ).toBeEnabled();
    expect(api.refreshTrustedLists).toHaveBeenCalledWith("token-123");
  });

  it("imports a list file", async () => {
    api.importTrustedList.mockResolvedValue({ ...LISTS.lists[1], source: "import" });
    renderSettings();
    await screen.findByText("EU-Liste der Listen");

    const file = new File(["<x/>"], "TL-DE.xml", { type: "application/xml" });
    fireEvent.change(screen.getByLabelText("Liste importieren (XML)", { selector: "input" }), {
      target: { files: [file] },
    });

    expect(await screen.findByText("DE importiert.")).toBeInTheDocument();
    expect(api.importTrustedList).toHaveBeenCalledWith("token-123", file);
  });

  it("lists anchors with the internal CA not removable, adds and removes one", async () => {
    api.addIncomingTrustAnchor.mockResolvedValue(ANCHORS[1]);
    api.deleteIncomingTrustAnchor.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderSettings();

    expect(await screen.findByText("Interne Signatur-CA (OG Doc)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Interne Signatur-CA.*entfernen/ })).toBeNull();

    const form = screen.getByRole("form", { name: "Vertrauensanker hinzufügen" });
    fireEvent.change(within(form).getByLabelText("Bezeichnung"), { target: { value: "Neue CA" } });
    fireEvent.change(within(form).getByLabelText("CA-Zertifikat (PEM)"), {
      target: { value: "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Vertrauensanker hinzufügen" }));
    await waitFor(() =>
      expect(api.addIncomingTrustAnchor).toHaveBeenCalledWith(
        "token-123",
        "Neue CA",
        "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----"
      )
    );

    fireEvent.click(screen.getByRole("button", { name: "Partnerbehörde entfernen" }));
    await waitFor(() => expect(api.deleteIncomingTrustAnchor).toHaveBeenCalledWith("token-123", 7));
  });

  it("starts a backfill only with criteria and shows its progress", async () => {
    api.startBackfill.mockResolvedValue({ ...IDLE, state: "running", scanned: 4, queued: 2 });
    renderSettings();
    const form = await screen.findByRole("form", { name: "Bestand nachprüfen" });
    const start = within(form).getByRole("button", { name: "Nachprüfung starten" });
    expect(start).toBeDisabled();

    fireEvent.change(within(form).getByLabelText("Abgelegt ab"), { target: { value: "2026-11-01" } });
    api.getBackfillStatus.mockResolvedValue({ ...IDLE, state: "done", scanned: 10, queued: 3, skipped_validated: 7 });
    fireEvent.click(start);

    await waitFor(() =>
      expect(api.startBackfill).toHaveBeenCalledWith(
        "token-123",
        expect.objectContaining({ onlyUnvalidated: true, createdFrom: expect.stringContaining("2026-") })
      )
    );
    expect(await screen.findByTestId("backfill-status")).toHaveTextContent(/2 zur Prüfung eingereiht/);
    await waitFor(
      () => expect(screen.getByTestId("backfill-status")).toHaveTextContent(/Fertig: 10 Versionen/),
      { timeout: 5000 }
    );
  });
});
