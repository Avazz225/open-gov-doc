import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";
import { DerivedDocumentsPanel } from "@/components/DerivedDocumentsPanel";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary } from "@/lib/api";

const listDerivedDocumentsMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listDerivedDocuments: (...args: unknown[]) => listDerivedDocumentsMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
      permissions: [],
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
  deleted_by: null,
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  retention_until: null,
  full_deletion: false,
  pending_deletion_reason: null,
  registered_at: "2026-01-01T00:00:00Z",
  classification_level: null,
  derivation_type: null,
};

// Automated a11y regression net for the redaction badge fix (post-roadmap
// phase 31 session 8, ADR 0119) - `color-contrast` disabled, jsdom has no
// real rendering engine to compute it reliably (post-roadmap phase 33
// session 3, ADR 0137).
const AXE_OPTIONS = { rules: { "color-contrast": { enabled: false } } };

describe("DerivedDocumentsPanel accessibility", () => {
  it("has no axe violations with a redacted derived copy shown", async () => {
    listDerivedDocumentsMock.mockResolvedValue([
      { ...DOCUMENT, id: "doc-2", title: "Vertrag (geschwärzt)", derivation_type: "redaction" },
    ]);

    const { container } = render(
      <I18nProvider>
        <DerivedDocumentsPanel document={DOCUMENT} />
      </I18nProvider>
    );
    expect(await screen.findByText("Vertrag (geschwärzt)")).toBeInTheDocument();
    // Real accessible name, not just the neutral-bordered badge style
    // (ADR 0119 - deliberately its own `.badge.redacted`, not a reuse of
    // `.badge.classified`'s red tone).
    expect(screen.getByLabelText("Schwärzung")).toBeInTheDocument();

    expect(await axe(container, AXE_OPTIONS)).toHaveNoViolations();
  });
});
