import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RetimestampStatus } from "@/components/RetimestampStatus";
import { I18nProvider } from "@/i18n";

function renderRetimestampStatus() {
  return render(
    <I18nProvider>
      <RetimestampStatus />
    </I18nProvider>
  );
}

const listSignaturesDueForRetimestampMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listSignaturesDueForRetimestamp: (...args: unknown[]) =>
    listSignaturesDueForRetimestampMock(...args),
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

const DUE_SIGNATURE = {
  id: 1,
  document_id: "doc-1",
  version_number: 2,
  level: "aes" as const,
  connector_id: "internal",
  signer_display_name: "Alice",
  signed_at: "2025-01-01T10:00:00Z",
  last_timestamped_at: null,
};

describe("RetimestampStatus", () => {
  beforeEach(() => {
    listSignaturesDueForRetimestampMock.mockReset();
  });

  it("lists signatures due for archive-timestamp renewal", async () => {
    listSignaturesDueForRetimestampMock.mockResolvedValue([DUE_SIGNATURE]);

    renderRetimestampStatus();

    expect(await screen.findByText("doc-1")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("aes")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is due", async () => {
    listSignaturesDueForRetimestampMock.mockResolvedValue([]);

    renderRetimestampStatus();

    expect(await screen.findByText("Keine Signaturen derzeit fällig.")).toBeInTheDocument();
  });

  it("shows an unreachable state when signature-service is not reachable", async () => {
    listSignaturesDueForRetimestampMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderRetimestampStatus();

    expect(await screen.findByText(/Signature Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("has no retry/trigger button - deliberately read-only (ADR 0155)", async () => {
    listSignaturesDueForRetimestampMock.mockResolvedValue([DUE_SIGNATURE]);

    renderRetimestampStatus();
    await screen.findByText("doc-1");

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
