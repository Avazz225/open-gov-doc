import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeletionRegister } from "@/components/DeletionRegister";
import { I18nProvider } from "@/i18n";

function renderDeletionRegister() {
  return render(
    <I18nProvider>
      <DeletionRegister />
    </I18nProvider>
  );
}

const listDeletionRegisterMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listDeletionRegister: (...args: unknown[]) => listDeletionRegisterMock(...args),
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

describe("DeletionRegister", () => {
  beforeEach(() => {
    listDeletionRegisterMock.mockReset();
  });

  it("shows an empty-state message when there are no entries", async () => {
    listDeletionRegisterMock.mockResolvedValue([]);

    renderDeletionRegister();

    expect(await screen.findByText("Noch keine Einträge.")).toBeInTheDocument();
  });

  it("lists entries with resolved trigger labels", async () => {
    listDeletionRegisterMock.mockResolvedValue([
      {
        id: "reg-1",
        document_id: "doc-1",
        trigger: "forced_deletion",
        reason: "Aufbewahrungsfrist abgelaufen",
        triggered_by: "alice",
        occurred_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "reg-2",
        document_id: "doc-2",
        trigger: "trash_expiry",
        reason: null,
        triggered_by: null,
        occurred_at: "2026-01-02T00:00:00Z",
      },
    ]);

    renderDeletionRegister();

    expect(await screen.findByText("doc-1")).toBeInTheDocument();
    expect(screen.getByText("Zwangslöschung")).toBeInTheDocument();
    expect(screen.getByText("Aufbewahrungsfrist abgelaufen")).toBeInTheDocument();
    expect(screen.getByText("doc-2")).toBeInTheDocument();
    expect(screen.getByText("Papierkorb-Frist abgelaufen")).toBeInTheDocument();
  });

  it("shows an unreachable state when document-service is not reachable", async () => {
    listDeletionRegisterMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderDeletionRegister();

    expect(await screen.findByText("Document Service nicht erreichbar.")).toBeInTheDocument();
  });
});
