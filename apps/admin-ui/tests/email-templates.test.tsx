import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EmailTemplates } from "@/components/EmailTemplates";
import { I18nProvider } from "@/i18n";

function renderEmailTemplates() {
  return render(
    <I18nProvider>
      <EmailTemplates />
    </I18nProvider>
  );
}

const listEmailTemplateUseCasesMock = vi.fn();
const listEmailTemplatesMock = vi.fn();
const putEmailTemplateMock = vi.fn();
const deleteEmailTemplateMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listEmailTemplateUseCases: (...args: unknown[]) => listEmailTemplateUseCasesMock(...args),
  listEmailTemplates: (...args: unknown[]) => listEmailTemplatesMock(...args),
  putEmailTemplate: (...args: unknown[]) => putEmailTemplateMock(...args),
  deleteEmailTemplate: (...args: unknown[]) => deleteEmailTemplateMock(...args),
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

const USE_CASES = [
  {
    use_case: "document.deletion.reminder",
    description: "Löschfrist-Erinnerung für ein Dokument (5.2a)",
    placeholders: ["title", "document_id", "retention_until", "action", "link"],
  },
  {
    use_case: "license.invalid",
    description: "Lizenz ungültig (9.2)",
    placeholders: ["reason"],
  },
];

const TEMPLATE_A = {
  id: 1,
  use_case: "document.deletion.reminder",
  recipient_domain_pattern: null,
  subject_template: "Löschfrist erreicht bald: {title}",
  body_template: "Dokument {title} wird geloescht.",
  updated_at: "2026-01-01T00:00:00Z",
};

const TEMPLATE_B = {
  id: 2,
  use_case: "document.deletion.reminder",
  recipient_domain_pattern: "example.com",
  subject_template: "[example.com] {title}",
  body_template: "Domain-spezifisch.",
  updated_at: "2026-01-02T00:00:00Z",
};

describe("EmailTemplates", () => {
  beforeEach(() => {
    listEmailTemplateUseCasesMock.mockReset();
    listEmailTemplatesMock.mockReset();
    putEmailTemplateMock.mockReset();
    deleteEmailTemplateMock.mockReset();
  });

  it("shows an empty state without any configured templates", async () => {
    listEmailTemplateUseCasesMock.mockResolvedValue(USE_CASES);
    listEmailTemplatesMock.mockResolvedValue([]);

    renderEmailTemplates();

    expect(
      await screen.findByText(/Noch keine Vorlagen konfiguriert/)
    ).toBeInTheDocument();
  });

  it("shows an unreachable state when notification-service is not reachable", async () => {
    listEmailTemplateUseCasesMock.mockRejectedValue(new TypeError("Failed to fetch"));
    listEmailTemplatesMock.mockResolvedValue([]);

    renderEmailTemplates();

    expect(
      await screen.findByText(/Notification Service nicht erreichbar/)
    ).toBeInTheDocument();
  });

  it("lists configured templates sorted by use case then domain, catch-all shown as 'Alle'", async () => {
    listEmailTemplateUseCasesMock.mockResolvedValue(USE_CASES);
    listEmailTemplatesMock.mockResolvedValue([TEMPLATE_B, TEMPLATE_A]);

    renderEmailTemplates();

    const rows = await screen.findAllByRole("row");
    // Kopfzeile + zwei Datenzeilen, Catch-all (null) vor "example.com".
    expect(within(rows[1]).getByText("Alle")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Löschfrist erreicht bald: {title}")).toBeInTheDocument();
    expect(within(rows[2]).getByText("example.com")).toBeInTheDocument();
  });

  it("shows the placeholder hint for the currently selected use case", async () => {
    listEmailTemplateUseCasesMock.mockResolvedValue(USE_CASES);
    listEmailTemplatesMock.mockResolvedValue([]);

    renderEmailTemplates();

    expect(
      await screen.findByText(/\{title\}, \{document_id\}, \{retention_until\}, \{action\}, \{link\}/)
    ).toBeInTheDocument();
  });

  it("creates a new template and reloads", async () => {
    listEmailTemplateUseCasesMock.mockResolvedValue(USE_CASES);
    listEmailTemplatesMock.mockResolvedValue([]);
    putEmailTemplateMock.mockResolvedValue(TEMPLATE_A);

    renderEmailTemplates();
    await waitFor(() => expect(listEmailTemplatesMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Vorlage konfigurieren" });
    fireEvent.change(within(form).getByLabelText("Betreff"), {
      target: { value: "Löschfrist erreicht bald: {title}" },
    });
    fireEvent.change(within(form).getByLabelText("Text"), {
      target: { value: "Dokument {title} wird geloescht." },
    });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(putEmailTemplateMock).toHaveBeenCalledWith("token-123", "document.deletion.reminder", {
        recipientDomain: null,
        subjectTemplate: "Löschfrist erreicht bald: {title}",
        bodyTemplate: "Dokument {title} wird geloescht.",
      })
    );
    await waitFor(() => expect(listEmailTemplatesMock).toHaveBeenCalledTimes(2));
  });

  it("edits an existing row, locking use case and domain while editing", async () => {
    listEmailTemplateUseCasesMock.mockResolvedValue(USE_CASES);
    listEmailTemplatesMock.mockResolvedValue([TEMPLATE_B]);
    putEmailTemplateMock.mockResolvedValue(TEMPLATE_B);

    renderEmailTemplates();
    await screen.findByText("[example.com] {title}");

    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));

    const form = screen.getByRole("form", { name: "Vorlage konfigurieren" });
    expect(within(form).getByLabelText("Empfänger-Domain")).toHaveValue("example.com");
    expect(within(form).getByLabelText("Empfänger-Domain")).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("Text"), {
      target: { value: "Neuer Text." },
    });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(putEmailTemplateMock).toHaveBeenCalledWith("token-123", "document.deletion.reminder", {
        recipientDomain: "example.com",
        subjectTemplate: "[example.com] {title}",
        bodyTemplate: "Neuer Text.",
      })
    );
  });

  it("deletes a template and reloads", async () => {
    listEmailTemplateUseCasesMock.mockResolvedValue(USE_CASES);
    listEmailTemplatesMock.mockResolvedValue([TEMPLATE_A]);
    deleteEmailTemplateMock.mockResolvedValue(undefined);

    renderEmailTemplates();
    await screen.findByText("Löschfrist erreicht bald: {title}");

    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));

    await waitFor(() => expect(deleteEmailTemplateMock).toHaveBeenCalledWith("token-123", 1));
    await waitFor(() => expect(listEmailTemplatesMock).toHaveBeenCalledTimes(2));
  });

  it("shows an error when saving fails", async () => {
    listEmailTemplateUseCasesMock.mockResolvedValue(USE_CASES);
    listEmailTemplatesMock.mockResolvedValue([]);
    putEmailTemplateMock.mockRejectedValue(new Error("boom"));

    renderEmailTemplates();
    await waitFor(() => expect(listEmailTemplatesMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Vorlage konfigurieren" });
    fireEvent.change(within(form).getByLabelText("Betreff"), { target: { value: "S" } });
    fireEvent.change(within(form).getByLabelText("Text"), { target: { value: "B" } });
    fireEvent.submit(form);

    expect(await screen.findByText("Speichern fehlgeschlagen")).toBeInTheDocument();
  });
});
