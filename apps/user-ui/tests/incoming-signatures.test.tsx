import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IncomingSignaturesSection } from "@/components/IncomingSignaturesSection";
import { SignatureBadge } from "@/components/SignatureBadge";
import { I18nProvider } from "@/i18n";
import type {
  DocumentSummary,
  IncomingSignatureSummary,
  IncomingSignatureValidation,
} from "@/lib/api";
import { badgeKind, signerName } from "@/lib/incoming-signatures";

// Incoming signatures in the user UI (P92-S3, ADR 0248 Decision 7).

const getIncomingSignaturesMock = vi.fn();
const revalidateMock = vi.fn();
const downloadReportMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getIncomingSignatures: (...args: unknown[]) => getIncomingSignaturesMock(...args),
    revalidateIncomingSignatures: (...args: unknown[]) => revalidateMock(...args),
    downloadIncomingSignatureReport: (...args: unknown[]) => downloadReportMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const DOC = { id: "doc-1", current_version_number: 2 } as DocumentSummary;

function summary(over: Partial<IncomingSignatureSummary> = {}): IncomingSignatureSummary {
  return {
    version_number: 2,
    run_id: "run-1",
    signature_count: 1,
    worst_indication: "TOTAL-PASSED",
    best_qualification: "qes",
    changed_after_signing: false,
    truncated: false,
    validated_at: "2026-11-24T10:00:00Z",
    ...over,
  };
}

function row(over: Partial<IncomingSignatureValidation> = {}): IncomingSignatureValidation {
  return {
    run_id: "run-1",
    version_number: 2,
    trigger: "event",
    format: "pades",
    position: "Signature1",
    kind: "signature",
    indication: "TOTAL-PASSED",
    sub_indication: "OK",
    qualification: "qes",
    trust_source: "eu_trusted_list",
    signer_subject: "Common Name: Erika Mustermann, Organization: Behörde",
    signer_issuer: "Common Name: QTSP CA",
    claimed_signing_time: "2026-11-20T08:00:00Z",
    best_signature_time: "2026-11-20T08:00:05Z",
    coverage: "entire_document",
    qc_type: "esign",
    report_available: true,
    failure_reason: null,
    validated_at: "2026-11-24T10:00:00Z",
    ...over,
  };
}

function renderWithI18n(node: React.ReactNode) {
  return render(<I18nProvider>{node}</I18nProvider>);
}

describe("badge kind", () => {
  it("maps a summary to qualified, valid, invalid or not checkable", () => {
    expect(badgeKind(summary())).toBe("qualified");
    expect(badgeKind(summary({ best_qualification: "ades" }))).toBe("valid");
    expect(badgeKind(summary({ worst_indication: "TOTAL-FAILED" }))).toBe("invalid");
    // A pass that doesn't cover what is shown is not a valid signature.
    expect(badgeKind(summary({ changed_after_signing: true }))).toBe("invalid");
    expect(badgeKind(summary({ worst_indication: "INDETERMINATE" }))).toBe("unchecked");
  });

  it("takes the common name out of the subject", () => {
    expect(signerName(row())).toBe("Erika Mustermann");
    expect(signerName(row({ signer_subject: "Organization: X" }))).toBe("Organization: X");
    expect(signerName(row({ signer_subject: null }))).toBeNull();
  });

  it("renders its label with tone and a hint", () => {
    renderWithI18n(<SignatureBadge summary={summary({ worst_indication: "INDETERMINATE" })} />);
    const badge = screen.getByTestId("signature-badge");
    expect(badge).toHaveTextContent("Signatur nicht prüfbar");
    expect(badge).toHaveClass("warn");
    expect(badge.getAttribute("title")).toContain("nicht abschließend prüfen");
  });
});

describe("IncomingSignaturesSection", () => {
  beforeEach(() => {
    getIncomingSignaturesMock.mockReset();
    revalidateMock.mockReset();
    downloadReportMock.mockReset();
  });

  it("lists the current version's signatures with qualification, time, coverage and trust", async () => {
    getIncomingSignaturesMock.mockResolvedValue({
      document_id: "doc-1",
      summaries: [summary({ version_number: 1, run_id: "old" }), summary()],
      validations: [
        row({ version_number: 1, run_id: "old", signer_subject: "Common Name: Alt" }),
        row(),
        row({
          kind: "document_timestamp",
          signer_subject: "Common Name: TSA",
          qualification: "unknown",
          trust_source: null,
        }),
      ],
    });

    renderWithI18n(<IncomingSignaturesSection document={DOC} />);

    expect(await screen.findByText("Erika Mustermann")).toBeInTheDocument();
    expect(screen.queryByText("Alt")).not.toBeInTheDocument();
    expect(screen.getByText("Qualifizierte elektronische Signatur (QES)")).toBeInTheDocument();
    expect(screen.getAllByText("Nachgewiesen signiert am").length).toBeGreaterThan(0);
    expect(screen.getAllByText("deckt das ganze Dokument ab").length).toBeGreaterThan(0);
    expect(screen.getByText("EU-Vertrauensliste")).toBeInTheDocument();
    expect(screen.getByText("Zeitstempel von TSA")).toBeInTheDocument();
    expect(screen.getByText("Qualifiziert signiert")).toBeInTheDocument();
  });

  it("names the source of a CAdES signature", async () => {
    getIncomingSignaturesMock.mockResolvedValue({
      document_id: "doc-1",
      summaries: [summary({ best_qualification: "ades" })],
      validations: [
        row({ format: "cades", position: "bescheid.txt.p7s", qualification: "ades" }),
        row({ format: "cades", position: "enveloping#2", qualification: "ades" }),
      ],
    });

    renderWithI18n(<IncomingSignaturesSection document={DOC} />);

    expect(await screen.findByText("Signaturdatei: bescheid.txt.p7s")).toBeInTheDocument();
    expect(screen.getByText("eingebettete Signatur (.p7m)")).toBeInTheDocument();
  });

  it("shows the reason of a failure in plain German", async () => {
    getIncomingSignaturesMock.mockResolvedValue({
      document_id: "doc-1",
      summaries: [summary({ worst_indication: "TOTAL-FAILED", changed_after_signing: true })],
      validations: [
        row({
          indication: "TOTAL-FAILED",
          qualification: "unknown",
          trust_source: null,
          best_signature_time: null,
          coverage: "changed_after_signing",
          failure_reason: "Dokument wurde nach dem Signieren unzulässig verändert",
        }),
      ],
    });

    renderWithI18n(<IncomingSignaturesSection document={DOC} />);

    expect(
      await screen.findByText("Dokument wurde nach dem Signieren unzulässig verändert")
    ).toBeInTheDocument();
    expect(screen.getByText("Angegebene Signaturzeit")).toBeInTheDocument();
    expect(screen.getByText("nach dem Signieren unzulässig verändert")).toBeInTheDocument();
    expect(screen.getByText("Signatur ungültig")).toBeInTheDocument();
  });

  it("says so when nothing was checked, and still offers a re-check", async () => {
    getIncomingSignaturesMock.mockResolvedValue({ document_id: "doc-1", summaries: [], validations: [] });

    renderWithI18n(<IncomingSignaturesSection document={DOC} />);

    expect(
      await screen.findByText("In dieser Version wurden keine Signaturen geprüft.")
    ).toBeInTheDocument();
    expect(screen.queryByText("Prüfbericht herunterladen")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Erneut prüfen" })).toBeEnabled();
  });

  it("downloads the report of the run", async () => {
    getIncomingSignaturesMock.mockResolvedValue({
      document_id: "doc-1",
      summaries: [summary()],
      validations: [row()],
    });
    downloadReportMock.mockResolvedValue(new Blob(["<x/>"]));
    const createUrl = vi.fn(() => "blob:report");
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    renderWithI18n(<IncomingSignaturesSection document={DOC} />);
    fireEvent.click(await screen.findByRole("button", { name: "Prüfbericht herunterladen" }));

    await waitFor(() => expect(downloadReportMock).toHaveBeenCalledWith("token-123", "run-1"));
    expect(createUrl).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    click.mockRestore();
  });

  it("asks for another run and polls until it shows up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      getIncomingSignaturesMock
        .mockResolvedValueOnce({ document_id: "doc-1", summaries: [summary()], validations: [row()] })
        .mockResolvedValueOnce({ document_id: "doc-1", summaries: [summary()], validations: [row()] })
        .mockResolvedValue({
          document_id: "doc-1",
          summaries: [summary({ run_id: "run-2" })],
          validations: [row({ run_id: "run-2", trigger: "revalidation" })],
        });
      revalidateMock.mockResolvedValue({ document_id: "doc-1", version_number: 2 });

      renderWithI18n(<IncomingSignaturesSection document={DOC} />);
      fireEvent.click(await screen.findByRole("button", { name: "Erneut prüfen" }));

      expect(await screen.findByRole("button", { name: "Prüfe erneut…" })).toBeDisabled();
      expect(revalidateMock).toHaveBeenCalledWith("token-123", "doc-1", 2);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3500);
      });
      expect(await screen.findByRole("button", { name: "Erneut prüfen" })).toBeEnabled();
      expect(getIncomingSignaturesMock).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});
