import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";
import { ClassificationPanel } from "@/components/ClassificationPanel";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    setDocumentClassificationLevel: vi.fn(),
  };
});

let mockPermissions: string[] = [];
vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
      permissions: mockPermissions,
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

// Automated a11y regression net for the badge/aria-label fixes (post-roadmap
// phase 31 session 8, ADR 0119) - `color-contrast` disabled, jsdom has no
// real rendering engine to compute it reliably (post-roadmap phase 33
// session 3, ADR 0137).
const AXE_OPTIONS = { rules: { "color-contrast": { enabled: false } } };

describe("ClassificationPanel accessibility", () => {
  it("has no axe violations for an unclassified document", async () => {
    const { container } = render(
      <I18nProvider>
        <ClassificationPanel document={DOCUMENT} onChanged={vi.fn()} />
      </I18nProvider>
    );
    expect(await screen.findByText("Nicht eingestuft")).toBeInTheDocument();

    expect(await axe(container, AXE_OPTIONS)).toHaveNoViolations();
  });

  it("has no axe violations for a classified document, incl. the raise control", async () => {
    mockPermissions = ["admin.classification"];
    const { container } = render(
      <I18nProvider>
        <ClassificationPanel
          document={{ ...DOCUMENT, classification_level: "VS-NfD" }}
          onChanged={vi.fn()}
        />
      </I18nProvider>
    );
    // Real accessible name ties the badge's meaning to "Einstufung", not
    // color/glyph alone (ADR 0119). Queried by label rather than
    // `findByText("VS-NfD")`, which would also match the raise-select's own
    // "VS-NfD" option and fail as ambiguous.
    expect(await screen.findByLabelText(/Einstufung: VS-NfD/)).toBeInTheDocument();

    expect(await axe(container, AXE_OPTIONS)).toHaveNoViolations();
    mockPermissions = [];
  });
});
