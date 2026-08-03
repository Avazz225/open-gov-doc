import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignaturesPanel } from "@/components/SignaturesPanel";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary } from "@/lib/api";

const listSignaturesMock = vi.fn();
const createSignatureMock = vi.fn();
const verifySignatureMock = vi.fn();
const listDocumentVersionsMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listSignatures: (...args: unknown[]) => listSignaturesMock(...args),
    createSignature: (...args: unknown[]) => createSignatureMock(...args),
    verifySignature: (...args: unknown[]) => verifySignatureMock(...args),
    listDocumentVersions: (...args: unknown[]) => listDocumentVersionsMock(...args),
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

const DOCUMENT: DocumentSummary = {
  id: "doc-1",
  title: "Vertrag",
  folder_id: null,
  object_type_id: null,
  attributes: {},
  current_version_number: 1,
  deleted_at: null,
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  retention_until: null,
  full_deletion: false,
  pending_deletion_reason: null,
};

function pdfVersion(versionNumber = 1) {
  return {
    version_number: versionNumber,
    filename: "vertrag.pdf",
    content_type: "application/pdf",
    size_bytes: 1234,
    checksum_sha256: "abc",
    is_conflict: false,
    based_on_version_number: null,
    comment: null,
    created_by: "alice",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function renderPanel() {
  return render(
    <I18nProvider>
      <SignaturesPanel document={DOCUMENT} />
    </I18nProvider>
  );
}

describe("SignaturesPanel", () => {
  beforeEach(() => {
    listSignaturesMock.mockReset();
    createSignatureMock.mockReset();
    verifySignatureMock.mockReset();
    listDocumentVersionsMock.mockReset();
    listDocumentVersionsMock.mockResolvedValue([pdfVersion()]);
  });

  it("shows an empty-state message when no signatures exist yet", async () => {
    listSignaturesMock.mockResolvedValue([]);

    renderPanel();

    expect(await screen.findByText(/noch nicht signiert/)).toBeInTheDocument();
  });

  it("lists an existing signature and shows the verification result", async () => {
    listSignaturesMock.mockResolvedValue([
      {
        id: 1,
        document_id: "doc-1",
        source_version_number: 1,
        version_number: 2,
        level: "aes",
        connector_id: "internal",
        signer_principal_id: "alice",
        signer_display_name: "Alice Test",
        certificate_subject: "CN=Alice Test",
        certificate_serial: "1",
        certificate_not_before: "2026-01-01T00:00:00Z",
        certificate_not_after: "2031-01-01T00:00:00Z",
        reason: null,
        signed_at: "2026-01-01T00:00:00Z",
      },
    ]);
    verifySignatureMock.mockResolvedValue({
      valid: true,
      integrity_intact: true,
      certificate_expired: false,
      errors: [],
    });

    renderPanel();

    // Gezielt auf das Level-Badge der Signatur beschränkt - die
    // Signieren-Auswahl darunter hat ebenfalls eine "AES"-Option.
    expect(await screen.findByText("AES", { selector: ".signature-level" })).toBeInTheDocument();
    expect(screen.getByText(/Alice Test/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Prüfen"));

    expect(await screen.findByText("Gültig")).toBeInTheDocument();
    expect(verifySignatureMock).toHaveBeenCalledWith("token-123", 1);
  });

  it("signs the document with the selected level and appends the new signature", async () => {
    listSignaturesMock.mockResolvedValue([]);
    createSignatureMock.mockResolvedValue({
      id: 2,
      document_id: "doc-1",
      source_version_number: 1,
      version_number: 2,
      level: "ses",
      connector_id: "internal",
      signer_principal_id: "alice",
      signer_display_name: "DMS System (SES)",
      certificate_subject: "CN=DMS System (SES)",
      certificate_serial: "2",
      certificate_not_before: "2026-01-01T00:00:00Z",
      certificate_not_after: "2031-01-01T00:00:00Z",
      reason: null,
      signed_at: "2026-01-01T00:00:00Z",
    });

    renderPanel();
    await waitFor(() => expect(listSignaturesMock).toHaveBeenCalled());

    fireEvent.click(screen.getByText("Jetzt signieren"));

    await waitFor(() =>
      expect(createSignatureMock).toHaveBeenCalledWith("token-123", {
        documentId: "doc-1",
        level: "ses",
        signerPrincipalId: "alice",
      })
    );
    expect(await screen.findByText(/DMS System \(SES\)/)).toBeInTheDocument();
  });

  it("hides the sign-offer for a non-PDF current version and shows a hint instead", async () => {
    listSignaturesMock.mockResolvedValue([]);
    listDocumentVersionsMock.mockResolvedValue([
      {
        ...pdfVersion(),
        filename: "notiz.txt",
        content_type: "text/plain",
      },
    ]);

    renderPanel();

    expect(await screen.findByText(/keine elektronische Signatur möglich/)).toBeInTheDocument();
    expect(screen.queryByText("Jetzt signieren")).not.toBeInTheDocument();
  });
});
